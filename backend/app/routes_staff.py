"""Модерация (модератор и администратор): очередь жалоб, карточка жалобы, ответы/шаблоны, предупреждения, профили со статусами.
Ограничение: ни модератор, ни админ не получают доступ к закрытому контенту и чатам — только к тому, что видно публично."""
from fastapi import APIRouter
from app.main import *  # noqa: F401,F403
from app.routes_social import card, post_out, n_out
from app.routes_chat import open_chat
from sqlalchemy import false

router = APIRouter(prefix="/staff")


# ---------- видимость контента для персонала ----------
def public_user(x: User | None) -> bool:
    return bool(is_active_user(x) and x.is_profile_public)


def staff_view(db: Session, rep: Report) -> dict:
    """Что модератору можно показать. Закрытое — только метаданные (hidden=True)."""
    t, cid = rep.content_type, rep.content_id
    hidden = {"hidden": True, "note": "Контент закрыт автором — недоступен модераторам"}
    if t == "support": return dict(type=t, hidden=False, text=rep.reason)
    if t == "post":
        p = db.get(Post, cid)
        if not p or p.removed_at: return dict(type=t, gone=True, hidden=False)
        if p.visibility == "public" and public_user(db.get(User, p.author_id)):
            d = post_out(db, p, None); return dict(type=t, hidden=False, text=d["text"], images=d["images"], tracks=[dict(id=x["id"], title=x["title"], artist=x["artist"]) for x in d["tracks"]])
        return dict(type=t, **hidden)
    if t == "comment":
        c = db.get(Comment, cid)
        if not c or c.removed_at: return dict(type=t, gone=True, hidden=False)
        return dict(type=t, hidden=False, text=c.text) if track_in_public_playlist(db, c.track_id) else dict(type=t, **hidden)
    if t == "playlist":
        p = db.get(Playlist, cid)
        if not p or p.removed_at: return dict(type=t, gone=True, hidden=False)
        if p.is_public and public_user(db.get(User, p.owner_id)): return dict(type=t, hidden=False, name=p.name, text=p.description)
        return dict(type=t, **hidden)
    if t == "track":
        tr = db.get(Track, cid)
        if not tr or tr.removed_at: return dict(type=t, gone=True, hidden=False)
        return dict(type=t, hidden=False, title=tr.title, artist=tr.artist, text=tr.description) if track_in_public_playlist(db, cid) else dict(type=t, **hidden)
    x = db.get(User, cid)
    if not x: return dict(type=t, gone=True, hidden=False)
    return dict(type=t, hidden=False, name=x.name, text=x.about) if public_user(x) else dict(type=t, **hidden)


def status_of(db: Session, x: User) -> dict:
    """Статусы профиля, видимые персоналу (без закрытого содержимого)."""
    warns = db.scalars(select(ModWarning).where(ModWarning.user_id == x.id).order_by(ModWarning.id.desc())).all()
    pend = db.scalar(select(DeletionRequest.id).where(DeletionRequest.user_id == x.id, DeletionRequest.status == "pending"))
    return dict(id=x.id, name=x.name, nickname=x.nickname, avatar_url=x.avatar_url if x.is_profile_public else "", role=x.role, created_at=iso(x.created_at),
                profile="public" if x.is_profile_public else "private", banned=x.is_banned, deleted=x.deleted_at is not None,
                deletion_pending=pend is not None, active_warnings=sum(1 for w in warns if not w.revoked_at), total_warnings=len(warns),
                blocks_made=db.scalar(select(func.count()).select_from(Block).where(Block.blocker_id == x.id)),
                blocked_by=db.scalar(select(func.count()).select_from(Block).where(Block.blocked_id == x.id)))


def report_row(db: Session, r: Report) -> dict:
    rep_u, tgt = db.get(User, r.reporter_id), db.get(User, r.target_user_id) if r.target_user_id else None
    return dict(id=r.id, content_type=r.content_type, content_id=r.content_id, category=r.category, status=r.status, priority=r.priority,
                reporter=dict(id=rep_u.id, name=rep_u.name) if rep_u else None, target=dict(id=tgt.id, name=tgt.name) if tgt else None,
                assigned_to=r.assigned_to, created_at=iso(r.created_at))


@router.get("/reports")
def report_queue(content_type: str | None = None, status: str | None = None, priority: str | None = None, q: str = "",
                 u: User = Depends(need_staff), db: Session = Depends(get_db)):
    s = select(Report)
    if content_type: s = s.where(Report.content_type == content_type)
    if status: s = s.where(Report.status == status)
    if priority: s = s.where(Report.priority == priority)
    order = case((Report.priority == "high", 0), (Report.priority == "normal", 1), else_=2)
    rows = db.scalars(s.order_by(order, Report.id.desc()).limit(200)).all()
    out = [report_row(db, r) for r in rows]
    if q.strip(): out = [r for r in out if like(q.strip(), r["reporter"]["name"] if r["reporter"] else "", r["target"]["name"] if r["target"] else "", str(r["id"]))]
    return dict(items=out, counts={k: db.scalar(select(func.count()).select_from(Report).where(Report.status == k)) for k in ("open", "in_progress", "closed")})


@router.get("/reports/{rid}")
def report_card(rid: int, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r: raise HTTPException(404, "Жалоба не найдена")
    replies = db.execute(select(ReportReply, User.name).join(User, User.id == ReportReply.author_id).where(ReportReply.report_id == rid).order_by(ReportReply.id)).all()
    same = db.scalars(select(Report).where(Report.content_type == r.content_type, Report.content_id == r.content_id, Report.id != rid).order_by(Report.id.desc()).limit(20)).all() if r.content_id else []
    cond = AuditLog.target_user_id == r.target_user_id if r.target_user_id else false()
    acts = db.scalars(select(AuditLog).where(or_(and_(AuditLog.target_type == "report", AuditLog.target_id == rid), cond)).order_by(AuditLog.id.desc()).limit(30)).all()
    view = staff_view(db, r)
    return dict(report=report_row(db, r), reason=r.reason, resolution=r.resolution, content=view, can_delete_content=bool(r.content_type in ("post", "comment", "playlist", "track") and not view.get("hidden") and not view.get("gone")),
                author=status_of(db, db.get(User, r.target_user_id)) if r.target_user_id and db.get(User, r.target_user_id) else None,
                reporter=status_of(db, db.get(User, r.reporter_id)),
                replies=[dict(id=x.id, author=n, text=x.text, created_at=iso(x.created_at)) for x, n in replies],
                history=dict(other_reports=[report_row(db, o) for o in same],
                             actions=[dict(id=a.id, action=a.action, actor_role=a.actor_role, created_at=iso(a.created_at)) for a in acts]))


class AssignIn(BaseModel):
    priority: Literal["low", "normal", "high"] | None = None
    status: Literal["open", "in_progress"] | None = None
    take: bool = False


@router.patch("/reports/{rid}")
def update_report(rid: int, a: AssignIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r or r.status == "closed": raise HTTPException(404, "Жалоба не найдена или закрыта")
    if a.priority: r.priority = a.priority
    if a.status: r.status = a.status
    if a.take: r.assigned_to = u.id; r.status = "in_progress"
    audit(db, u, "report_update", "report", rid, r.target_user_id, priority=a.priority, status=a.status, take=a.take)
    db.commit()
    return report_row(db, r)


def remove_content(db: Session, r: Report, u: User, reason: str):
    view = staff_view(db, r)
    if r.content_type not in ("post", "comment", "playlist", "track") or view.get("hidden") or view.get("gone"):
        raise HTTPException(403, "Контент закрыт или удалён — удаление недоступно")
    M = {"post": Post, "comment": Comment, "playlist": Playlist, "track": Track}[r.content_type]
    obj = db.get(M, r.content_id); obj.removed_at = now()
    return obj


class ContentActionIn(Strip):
    reason: str = Field("", max_length=500)


@router.post("/reports/{rid}/delete-content")
def delete_content(rid: int, a: ContentActionIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r: raise HTTPException(404, "Жалоба не найдена")
    obj = remove_content(db, r, u, a.reason)
    audit(db, u, "delete_content", r.content_type, r.content_id, r.target_user_id, report_id=rid, reason=a.reason)
    if r.target_user_id: notify(db, r.target_user_id, "content_removed", f"Ваш контент удалён модерацией{': ' + a.reason if a.reason else ''}", u, r.content_type, r.content_id)
    db.commit()
    return {"ok": True}


class WarnIn(Strip):
    user_id: int | None = None
    nickname: str | None = None
    reason: str = Field(min_length=3, max_length=500)
    report_id: int | None = None


def issue_warning(db: Session, u: User, user_id: int, reason: str, report_id: int | None):
    x = db.get(User, user_id)
    if not x or x.deleted_at: raise HTTPException(404, "Пользователь не найден")
    if x.role == "admin" or (x.role == "moderator" and u.role != "admin"): raise HTTPException(403, "Нельзя выдать предупреждение этому пользователю")
    w = ModWarning(user_id=x.id, issued_by=u.id, reason=reason, report_id=report_id); db.add(w); db.flush()
    audit(db, u, "warn", "user", x.id, x.id, warning_id=w.id, reason=reason, report_id=report_id)
    notify(db, x.id, "warning", f"Предупреждение от модерации: {reason}", u, "warning", w.id)
    return w


@router.post("/reports/{rid}/warn", status_code=201)
def warn_from_report(rid: int, a: ContentActionIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r or not r.target_user_id: raise HTTPException(404, "У жалобы нет автора контента")
    issue_warning(db, u, r.target_user_id, a.reason or "Нарушение правил платформы", rid); db.commit()
    return {"ok": True}


@router.post("/reports/{rid}/request-deletion", status_code=201)
def request_deletion_from_report(rid: int, a: ContentActionIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r or not r.target_user_id: raise HTTPException(404, "У жалобы нет автора контента")
    return create_deletion_request(db, u, r.target_user_id, a.reason or "Нарушение правил платформы", rid)


class ReplyIn(Strip):
    text: str = Field(min_length=1, max_length=2000)
    close: bool = True
    resolution: str = Field("", max_length=500)


@router.post("/reports/{rid}/reply")
def reply_report(rid: int, a: ReplyIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    """Ответ автору жалобы (в чат поддержки + уведомление) и, по желанию, закрытие."""
    r = db.get(Report, rid)
    if not r: raise HTTPException(404, "Жалоба не найдена")
    reporter = db.get(User, r.reporter_id)
    if not is_active_user(reporter): raise HTTPException(404, "Автор жалобы недоступен")
    db.add(ReportReply(report_id=rid, author_id=u.id, text=a.text))
    chat = open_chat(db, u, reporter, "support")
    db.add(Message(chat_id=chat.id, sender_id=u.id, kind="text", text=f"[Жалоба №{rid}] {a.text}"))
    notify(db, reporter.id, "report_reply", f"Ответ модерации по обращению №{rid}", u, "report", rid)
    audit(db, u, "reply_report", "report", rid, r.target_user_id, closed=a.close)
    if a.close:
        r.status, r.closed_at, r.resolution = "closed", now(), a.resolution or a.text[:200]
        audit(db, u, "close_report", "report", rid, r.target_user_id)
    elif r.status == "open": r.status, r.assigned_to = "in_progress", u.id
    db.commit()
    return {"ok": True}


@router.post("/reports/{rid}/close")
def close_report(rid: int, a: ContentActionIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r or r.status == "closed": raise HTTPException(404, "Жалоба не найдена или уже закрыта")
    r.status, r.closed_at, r.resolution = "closed", now(), a.reason
    audit(db, u, "close_report", "report", rid, r.target_user_id, reason=a.reason); db.commit()
    return {"ok": True}


# ---------- шаблоны ответов ----------
class TemplateIn(Strip):
    title: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)


@router.get("/templates")
def templates(u: User = Depends(need_staff), db: Session = Depends(get_db)):
    return [dict(id=t.id, title=t.title, text=t.text) for t in db.scalars(select(ReplyTemplate).order_by(ReplyTemplate.id))]


@router.post("/templates", status_code=201)
def add_template(t: TemplateIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    db.add(ReplyTemplate(title=t.title, text=t.text, created_by=u.id)); db.commit()
    return {"ok": True}


@router.delete("/templates/{tid}", status_code=204)
def del_template(tid: int, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    t = db.get(ReplyTemplate, tid)
    if t: db.delete(t); db.commit()
    return Response(status_code=204)


# ---------- предупреждения ----------
@router.get("/warnings")
def warnings(status: str = "all", user_id: int | None = None, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    s = select(ModWarning, User.name, User.nickname).join(User, User.id == ModWarning.user_id)
    if status == "active": s = s.where(ModWarning.revoked_at.is_(None))
    if status == "revoked": s = s.where(ModWarning.revoked_at.is_not(None))
    if user_id: s = s.where(ModWarning.user_id == user_id)
    return [dict(id=w.id, user=dict(id=w.user_id, name=n, nickname=nk), reason=w.reason, issued_by=w.issued_by, report_id=w.report_id, created_at=iso(w.created_at), revoked=w.revoked_at is not None)
            for w, n, nk in db.execute(s.order_by(ModWarning.id.desc()).limit(200))]


@router.post("/warnings", status_code=201)
def new_warning(w: WarnIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    uid = w.user_id
    if uid is None and w.nickname:                        # пользователя указывают по нику, а не по числовому ID
        uid = db.scalar(select(User.id).where(User.nickname == w.nickname.strip().lower()))
    if uid is None: raise HTTPException(404, "Пользователь с таким ником не найден")
    issue_warning(db, u, uid, w.reason, w.report_id); db.commit()
    return {"ok": True}


@router.post("/warnings/{wid}/revoke")
def revoke_warning(wid: int, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    w = db.get(ModWarning, wid)
    if not w or w.revoked_at: raise HTTPException(404, "Предупреждение не найдено")
    if u.role != "admin" and w.issued_by != u.id: raise HTTPException(403, "Модератор отзывает только свои предупреждения")
    w.revoked_at, w.revoked_by = now(), u.id
    audit(db, u, "revoke_warning", "user", w.user_id, w.user_id, warning_id=wid)
    notify(db, w.user_id, "warning_revoked", "Предупреждение отозвано модерацией", u, "warning", wid)
    db.commit()
    return {"ok": True}


# ---------- профили со статусами (без закрытого содержимого) ----------
@router.get("/users")
def staff_users(q: str = "", profile: str | None = None, flag: str | None = None, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    rows = [status_of(db, x) for x in db.scalars(select(User).order_by(User.id.desc()).limit(500))]
    if q.strip(): rows = [r for r in rows if like(q.strip(), r["name"], r["nickname"] or "")]
    if profile: rows = [r for r in rows if r["profile"] == profile]
    if flag == "warned": rows = [r for r in rows if r["active_warnings"]]
    if flag == "banned": rows = [r for r in rows if r["banned"]]
    if flag == "blocked": rows = [r for r in rows if r["blocked_by"]]
    return rows[:100]


@router.get("/users/{uid}")
def staff_user(uid: int, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    x = db.get(User, uid)
    if not x: raise HTTPException(404, "Пользователь не найден")
    d = status_of(db, x)
    d["public"] = dict(about=x.about, music_interests=x.music_interests, favorite_artists=x.favorite_artists) if public_user(x) else None   # закрытое не показываем
    d["warnings"] = [dict(id=w.id, reason=w.reason, created_at=iso(w.created_at), revoked=w.revoked_at is not None) for w in db.scalars(select(ModWarning).where(ModWarning.user_id == uid).order_by(ModWarning.id.desc()))]
    return d


# ---------- запросы на удаление профиля ----------
def create_deletion_request(db: Session, u: User, uid: int, reason: str, report_id: int | None = None) -> dict:
    x = db.get(User, uid)
    if not x or x.deleted_at: raise HTTPException(404, "Пользователь не найден")
    if x.id == u.id or x.role == "admin": raise HTTPException(403, "Этот профиль нельзя удалить")
    if x.role == "moderator" and u.role != "admin": raise HTTPException(403, "Профиль модератора может удалить только администратор")
    if db.scalar(select(DeletionRequest.id).where(DeletionRequest.user_id == uid, DeletionRequest.status == "pending")): raise HTTPException(400, "Запрос уже создан")
    d = DeletionRequest(user_id=uid, requested_by=u.id, reason=reason, report_id=report_id); db.add(d); db.flush()
    audit(db, u, "request_deletion", "user", uid, uid, request_id=d.id, reason=reason, report_id=report_id)
    db.commit()
    return {"id": d.id, "status": "pending"}


class DeletionIn(Strip):
    user_id: int
    reason: str = Field(min_length=3, max_length=500)


@router.post("/deletion-requests", status_code=201)
def request_deletion(a: DeletionIn, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    """Модератор/админ только ИНИЦИИРУЕТ — удаление вступает в силу после подтверждения администратором."""
    return create_deletion_request(db, u, a.user_id, a.reason)


@router.get("/deletion-requests/mine")
def my_deletion_requests(u: User = Depends(need_staff), db: Session = Depends(get_db)):
    rows = db.execute(select(DeletionRequest, User.name).join(User, User.id == DeletionRequest.user_id).where(DeletionRequest.requested_by == u.id).order_by(DeletionRequest.id.desc()).limit(50)).all()
    return [dict(id=d.id, user=dict(id=d.user_id, name=n), reason=d.reason, status=d.status, created_at=iso(d.created_at)) for d, n in rows]


# ---------- единый список обращений (для раздела «Уведомления») ----------
@router.get("/inbox")
def staff_inbox(u: User = Depends(need_staff), db: Session = Depends(get_db)):
    """Жалобы, обращения в поддержку, сообщения пользователей, запросы на удаление профилей и предупреждения — одним списком."""
    who = lambda x: dict(id=x.id, name=x.name, nickname=x.nickname) if x else None
    items = []
    for r in db.scalars(select(Report).where(Report.status != "closed").order_by(Report.id.desc()).limit(150)):
        sup = r.content_type == "support"
        items.append(dict(key=f"r{r.id}", kind="support" if sup else "report", title=f"{'Обращение в поддержку' if sup else 'Жалоба'} №{r.id}",
                          text=(r.reason or r.category)[:160], user=who(db.get(User, r.reporter_id)), status=r.status, urgent=r.priority == "high",
                          created_at=iso(r.created_at), ref=dict(page="staff-report", id=r.id)))
    for c in db.scalars(select(Chat).where(Chat.kind == "support", or_(Chat.user_a_id == u.id, Chat.user_b_id == u.id))):
        other = c.user_b_id if c.user_a_id == u.id else c.user_a_id
        unread = db.scalars(select(Message).where(Message.chat_id == c.id, Message.sender_id != u.id, Message.read_at.is_(None)).order_by(Message.id.desc())).all()
        if unread: items.append(dict(key=f"m{c.id}", kind="message", title=f"Сообщений от пользователя: {len(unread)}", text=(unread[0].text or "")[:160],
                                     user=who(db.get(User, other)), status="unread", urgent=False, created_at=iso(unread[0].created_at), ref=dict(page="chats", id=c.id)))
    dq = select(DeletionRequest).order_by(DeletionRequest.id.desc()).limit(60)
    dq = dq.where(DeletionRequest.status == "pending") if u.role == "admin" else dq.where(DeletionRequest.requested_by == u.id)
    for d in db.scalars(dq):
        items.append(dict(key=f"d{d.id}", kind="deletion", title=f"Запрос на удаление профиля №{d.id}", text=d.reason[:160], user=who(db.get(User, d.user_id)),
                          status=d.status, urgent=d.status == "pending" and u.role == "admin", created_at=iso(d.created_at), ref=dict(page="admin-deletions" if u.role == "admin" else "staff-users")))
    since = now() - dt.timedelta(days=14)
    wq = select(ModWarning).where(ModWarning.created_at >= since).order_by(ModWarning.id.desc()).limit(60)
    if u.role != "admin": wq = wq.where(ModWarning.issued_by == u.id)
    for w in db.scalars(wq):
        items.append(dict(key=f"w{w.id}", kind="warning", title="Предупреждение" + (" (отозвано)" if w.revoked_at else ""), text=w.reason[:160], user=who(db.get(User, w.user_id)),
                          status="revoked" if w.revoked_at else "active", urgent=False, created_at=iso(w.created_at), ref=dict(page="staff-warnings")))
    items.sort(key=lambda r: r["created_at"], reverse=True)
    counts = {}
    for i in items: counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return dict(items=items, counts=counts)


# ---------- история действий ----------
def log_out(db: Session, a: AuditLog) -> dict:
    actor, tgt = db.get(User, a.actor_id), db.get(User, a.target_user_id) if a.target_user_id else None
    return dict(id=a.id, actor=dict(id=a.actor_id, name=actor.name if actor else "?"), actor_role=a.actor_role, action=a.action, target_type=a.target_type,
                target_id=a.target_id, target_user=dict(id=tgt.id, name=tgt.name) if tgt else None, created_at=iso(a.created_at))


@router.get("/actions")
def my_actions(action: str | None = None, u: User = Depends(need_staff), db: Session = Depends(get_db)):
    s = select(AuditLog).where(AuditLog.actor_id == u.id)
    if action: s = s.where(AuditLog.action == action)
    return [log_out(db, a) for a in db.scalars(s.order_by(AuditLog.id.desc()).limit(200))]
