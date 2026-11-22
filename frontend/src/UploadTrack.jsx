import React, { useState } from 'react';
import { X, Upload, Music, Image as ImageIcon, Check } from 'lucide-react';
import { Combobox } from './ui.jsx';

const MAX_MB = 50;
const UNKNOWN = ['', 'unknown', 'unknown artist', 'неизвестный исполнитель'];

// «Исполнитель - Название.mp3» → { artist, title } (те же правила, что и на сервере)
function guessFromName(filename) {
  const stem = filename.replace(/\.[^/.]+$/, '').replace(/_/g, ' ').trim();
  const i = stem.indexOf(' - ');
  if (i > 0 && stem.slice(i + 3).trim()) return { artist: stem.slice(0, i).trim(), title: stem.slice(i + 3).trim() };
  return { artist: '', title: stem };
}

export default function UploadTrack({ api, run, onUploaded, onCancel, options = {} }) {
  const [file, setFile] = useState(null);
  const [cover, setCover] = useState(null);
  const [coverPreview, setCoverPreview] = useState(null);
  const [formData, setFormData] = useState({ title: '', artist: '', genre: '', mood: '', description: '' });
  const [isDragging, setIsDragging] = useState(false);
  const [fileError, setFileError] = useState('');

  const artistUnknown = file && UNKNOWN.includes(formData.artist.trim().toLowerCase());

  const pickFile = (f) => {
    if (!f) return;
    const ok = /\.(mp3|wav)$/i.test(f.name);
    if (!ok) { setFileError('Допустимы только файлы MP3 и WAV'); return; }
    if (f.size > MAX_MB * 1048576) { setFileError(`Файл больше ${MAX_MB} МБ`); return; }
    setFileError(''); setFile(f);
    const g = guessFromName(f.name);
    setFormData(prev => ({ ...prev, title: g.title, artist: g.artist }));
  };

  const handleCoverChange = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setCover(f);
    const reader = new FileReader();
    reader.onload = (ev) => setCoverPreview(ev.target.result);
    reader.readAsDataURL(f);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    if (cover) fd.append('cover', cover);
    Object.entries(formData).forEach(([k, v]) => { if (v.trim()) fd.append(k, v.trim()); });
    await run(async () => {
      await api('/tracks', { method: 'POST', body: fd });
      onUploaded();
    });
  };

  return (
    <div className="upload-page">
      <div className="upload-header">
        <h2><Upload size={22} /> Загрузка трека</h2>
        <button className="icon-btn" onClick={onCancel}><X size={20} /></button>
      </div>
      <form onSubmit={handleSubmit} className="upload-form">
        <div
          className={`upload-zone ${isDragging ? 'active' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => { e.preventDefault(); setIsDragging(false); pickFile(e.dataTransfer.files?.[0]); }}
        >
          <input type="file" accept=".mp3,.wav,audio/mpeg,audio/wav" onChange={(e) => pickFile(e.target.files?.[0])} hidden id="audio-file" />
          <label htmlFor="audio-file" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '16px', width: '100%' }}>
            <Music size={32} />
            <div>
              <b>{file ? file.name : 'Перетащите аудиофайл или нажмите для выбора'}</b>
              <div className="mut">MP3 или WAV, до {MAX_MB} МБ</div>
            </div>
          </label>
        </div>
        {fileError && <div className="field-error">{fileError}</div>}

        <div className="field-row">
          <div className="field">
            <label>Название</label>
            <input value={formData.title} maxLength={200} onChange={e => setFormData({ ...formData, title: e.target.value })} required />
          </div>
          <div className="field">
            <label>Исполнитель</label>
            <Combobox value={formData.artist} maxLength={200} onChange={v => setFormData({ ...formData, artist: v })} options={options.artists || []} placeholder="Выберите или введите исполнителя" />
            {artistUnknown && <div className="field-warn">Не удалось определить исполнителя по имени файла — укажите вручную (иначе будет «Неизвестный исполнитель»).</div>}
          </div>
        </div>

        <div className="field-row">
          <div className="field">
            <label>Жанр</label>
            <Combobox value={formData.genre} onChange={v => setFormData({ ...formData, genre: v })} options={options.genres || []} placeholder="Выберите или введите жанр" />
          </div>
          <div className="field">
            <label>Настроение</label>
            <Combobox value={formData.mood} onChange={v => setFormData({ ...formData, mood: v })} options={options.moods || []} placeholder="Выберите или введите настроение" />
          </div>
        </div>

        <div className="field">
          <label>Описание</label>
          <textarea rows="3" maxLength={2000} value={formData.description} onChange={e => setFormData({ ...formData, description: e.target.value })} />
        </div>

        <div className="field">
          <label>Обложка (необязательно)</label>
          <label className="upload-zone cover-zone">
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={handleCoverChange} hidden />
            {coverPreview ? <img src={coverPreview} className="cover-preview" alt="Cover" /> : <><ImageIcon size={32} /><div><b>Загрузить обложку</b></div></>}
          </label>
        </div>

        <div className="upload-actions">
          <button type="button" className="del" onClick={onCancel}>Отмена</button>
          <button type="submit" className="pri" disabled={!file || !formData.title.trim()}><Check size={16} /> Загрузить трек</button>
        </div>
      </form>
    </div>
  );
}
