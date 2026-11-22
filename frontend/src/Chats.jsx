import React, { useState, useEffect, useRef } from 'react'
import { Send, Plus, Play, ArrowLeft, Flame, ShieldAlert, Music, ListMusic } from 'lucide-react'
import Mascot from './Mascot.jsx'
import { Avatar, RoleBadge, Seg, Modal, fmtDate } from './ui.jsx'

/** Медуза-питомец чата: растёт от сообщений, серия (🔥) — дни подряд, когда писали оба. */
export function Pet({ pet, compact }) {
  if (!pet) return null
  const state = pet.needs_reply ? 'thinking' : pet.streak_today ? 'active' : 'idle'
  const size = (compact ? 26 : 54) + pet.stage * (compact ? 3 : 12)
  return (
    <div className={`pet pet--s${pet.stage} ${compact ? 'pet--compact' : ''}`} title={`${pet.stage_name} · ${pet.xp} оп.`}>
      <Mascot state={state} size={size} />
      {!compact && <div className="pet-info"><b>{pet.stage_name}</b>
        <div className="pet-bar"><i style={{ width: `${pet.progress}%` }} /></div>
        <span className="mut">{pet.next_at ? `до следующей стадии: ${pet.next_at - pet.xp}` : 'максимальная стадия'}</span></div>}
      <span className={`streak ${pet.streak ? 'on' : ''}`}><Flame size={compact ? 12 : 16} />{pet.streak}</span>
    </div>
  )
}

function Shared({ m, c }) {
  const [d, setD] = useState(null); const [bad, setBad] = useState(false)
  useEffect(() => {
    const path = { track: `/tracks/${m.ref_id}`, playlist: `/playlists/${m.ref_id}`, post: `/posts/${m.ref_id}` }[m.kind]
    c.api(path).then(setD).catch(() => setBad(true))
  }, [m.id])
  if (bad) return <div className="shared mut">Контент недоступен</div>
  if (!d) return <div className="shared mut">…</div>
  if (m.kind === 'track') return <div className="shared"><button className="icon-btn pri" onClick={() => { c.setPlayContext({ tracks: [d], index: 0 }); c.setPlay(d) }}><Play size={14} /></button><span><b>{d.title}</b><small className="mut"> {d.artist}</small></span></div>
  if (m.kind === 'playlist') return <button className="shared" onClick={() => { window.location.hash = `#/playlist/${d.id}` }}><ListMusic size={16} /> <span><b>{d.name}</b><small className="mut"> {d.tracks_count} треков</small></span></button>
  return <div className="shared"><span><b>{d.author.name}</b><small className="mut"> {d.text.slice(0, 90) || 'пост'}</small></span></div>
}

export function Chats({ open, ...c }) {
  const [list, setList] = useState([]); const [active, setActive] = useState(open || null); const [data, setData] = useState(null)
  const [text, setText] = useState(''); const [share, setShare] = useState(false); const [tab, setTab] = useState('track'); const [mine, setMine] = useState({ track: [], playlist: [] })
  const end = useRef(null)
  const loadList = () => c.api('/chats').then(setList).catch(() => {})
  const loadChat = (id) => c.api(`/chats/${id}`).then(setData).catch(() => { setActive(null); setData(null) })
  useEffect(() => { loadList(); const t = setInterval(loadList, 15000); return () => clearInterval(t) }, [])
  useEffect(() => { if (!active) return; loadChat(active); const t = setInterval(() => loadChat(active), 5000); return () => clearInterval(t) }, [active])
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [data?.messages?.length])
  const send = (body) => c.run(async () => { setData(await c.api(`/chats/${active}/messages`, { method: 'POST', body })); loadList() })
  const submit = () => { if (!text.trim()) return; send({ kind: 'text', text }); setText('') }
  const openShare = () => c.run(async () => { setMine({ track: await c.api('/tracks'), playlist: (await c.api('/playlists')).filter(p => p.is_owner) }); setShare(true) })

  if (active && data) {
    const ch = data.chat
    return (
      <div className={`chat ${ch.with_staff ? 'chat--staff' : ''}`}>
        <div className="chat-head">
          <button className="icon-btn" onClick={() => { setActive(null); setData(null); loadList(); c.refreshMe() }}><ArrowLeft size={16} /></button>
          <button className="link-user" onClick={() => !ch.with_staff && c.go('user', { id: ch.other.id })}><Avatar u={ch.other} size={38} /><span><b>{ch.other.name}</b> <small className="nick">{ch.other.nickname}</small> <RoleBadge role={ch.other.role} /></span></button>
          {ch.with_staff && <span className="staff-tag"><ShieldAlert size={12} /> Модерация</span>}
        </div>
        {ch.pet && <div className="card pet-card"><Pet pet={ch.pet} /></div>}
        <div className="chat-msgs">
          {data.messages.map(m => (
            <div key={m.id} className={`bubble ${m.mine ? 'mine' : ''}`}>
              {m.kind !== 'text' && <Shared m={m} c={c} />}
              {m.text && <div>{m.text}</div>}
              <small>{fmtDate(m.created_at)}{m.mine && m.read ? ' · прочитано' : ''}</small>
            </div>))}
          {!data.messages.length && <div className="empty mut">Напишите первое сообщение{ch.pet ? ' — и медуза начнёт расти' : ''}.</div>}
          <div ref={end} />
        </div>
        <div className="chat-input">
          <button className="icon-btn" onClick={openShare} title="Поделиться треком или плейлистом"><Plus size={18} /></button>
          <input value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} placeholder="Сообщение…" />
          <button className="icon-btn pri" onClick={submit} disabled={!text.trim()}><Send size={16} /></button>
        </div>
        {share && (
          <Modal title="Поделиться" onClose={() => setShare(false)}>
            <Seg value={tab} onChange={setTab} options={[['track', 'Мои треки'], ['playlist', 'Мои плейлисты']]} />
            <div className="share-list">
              {mine[tab].map(x => <button key={x.id} className="row-btn" onClick={() => { send({ kind: tab, ref_id: x.id }); setShare(false) }}>{tab === 'track' ? <Music size={16} /> : <ListMusic size={16} />}<span>{x.title || x.name}</span></button>)}
              {!mine[tab].length && <p className="mut">Пока ничего нет.</p>}
            </div>
            <p className="mut">В чат можно отправлять текст и карточки контента. Голосовых и видео-сообщений нет.</p>
          </Modal>)}
      </div>
    )
  }
  return (
    <>
      <h2>Чаты</h2>
      {list.map(ch => (
        <div key={ch.id} className={`card chat-item ${ch.with_staff ? 'chat-item--staff' : ''}`} onClick={() => setActive(ch.id)}>
          <Avatar u={ch.other} size={46} />
          <div className="chat-item-body"><div className="row"><b>{ch.other.name}</b> <small className="nick">{ch.other.nickname}</small> <RoleBadge role={ch.other.role} />{ch.with_staff && <span className="staff-tag"><ShieldAlert size={12} /> Модерация</span>}</div>
            <span className="mut">{ch.last ? `${ch.last.mine ? 'Вы: ' : ''}${ch.last.text}` : 'Нет сообщений'}</span></div>
          <div className="chat-item-side"><Pet pet={ch.pet} compact />{ch.unread > 0 && <span className="badge">{ch.unread}</span>}</div>
        </div>))}
      {!list.length && <div className="empty"><Mascot state="idle" size={84} /><div className="mut">Чатов пока нет. Откройте чат с другом на странице «Друзья» — рядом с ним будет расти ваша медуза.</div></div>}
    </>
  )
}
