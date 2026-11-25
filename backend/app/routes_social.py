"""Соцсеть: друзья/подписки/блокировки, профили, посты и лента, поиск, уведомления, жалобы, соавторство, статистика."""
from fastapi import APIRouter
from sqlalchemy.exc import IntegrityError
from app.main import *  # noqa: F401,F403  (модели, зависимости и проверки доступа)

router = APIRouter()


# ---------- общие помощники ----------
def card(x: User) -> dict:
    return dict(id=x.id, name=x.name, nickname=x.nickname, avatar_url=x.avatar_url, role=x.role, is_profile_public=x.is_profile_public)


def visible_user(db: Session, viewer: User | None, uid: int) -> User:
    x = db.get(User, uid)
    if not is_active_user(x) or (viewer and blocked_between(db, viewer.id, uid)): raise HTTPException(404, "Пользователь не найден")
    return x


def relation(db: Session, me_: User, other_id: int) -> dict:
    out = dict(friend=are_friends(db, me_.id, other_id), request=None, request_id=None, following=False, follows_me=False)
    fr = db.scalar(select(Friendship).where(Friendship.status == "pending", or_(
        and_(Friendship.requester_id == me_.id, Friendship.addressee_id == other_id), and_(Friendship.requester_id == other_id, Friendship.addressee_id == me_.id))))
    if fr: out.update(request="outgoing" if fr.requester_id == me_.id else "incoming", request_id=fr.id)
    out["following"] = db.scalar(select(Follow.id).where(Follow.follower_id == me_.id, Follow.followee_id == other_id)) is not None
    out["follows_me"] = db.scalar(select(Follow.id).where(Follow.follower_id == other_id, Follow.followee_id == me_.id)) is not None
    return out


def users_by_ids(db: Session, ids, viewer: User) -> list[User]:
    hid = blocked_ids(db, viewer.id)
    rows = db.scalars(select(User).where(User.id.in_(list(ids)), User.deleted_at.is_(None), User.is_banned.is_(False))).all() if ids else []
    return sorted([x for x in rows if x.id not in hid], key=lambda x: x.name.casefold())


# ---------- друзья ----------
class UserRef(BaseModel):
    user_id: int


@router.get("/friends")
def friends_overview(u: User = Depends(need_user), db: Session = Depends(get_db)):
    fids = friend_ids(db, u.id)
    inc = db.scalars(select(Friendship).where(Friendship.addressee_id == u.id, Friendship.status == "pending")).all()
    out = db.scalars(select(Friendship).where(Friendship.requester_id == u.id, Friendship.status == "pending")).all()
    following = {r[0] for r in db.execute(select(Follow.followee_id).where(Follow.follower_id == u.id))}
    followers = {r[0] for r in db.execute(select(Follow.follower_id).where(Follow.followee_id == u.id))}
    fmt = lambda x, **kw: {**card(x), **kw}
    return dict(
        friends=[fmt(x, mutual_follow=x.id in following and x.id in followers) for x in users_by_ids(db, fids, u)],
        incoming=[fmt(x, request_id=r.id) for r in inc for x in users_by_ids(db, [r.requester_id], u)],
        outgoing=[fmt(x, request_id=r.id) for r in out for x in users_by_ids(db, [r.addressee_id], u)],
        following=[fmt(x, mutual=x.id in followers) for x in users_by_ids(db, following, u)],
        followers=[fmt(x, mutual=x.id in following) for x in users_by_ids(db, followers, u)])


@router.post("/friends/requests", status_code=201)
def send_request(r: UserRef, u: User = Depends(need_user), db: Session = Depends(get_db)):
    if r.user_id == u.id: raise HTTPException(400, "Нельзя добавить самого себя")
    x = visible_user(db, u, r.user_id)
    if are_friends(db, u.id, x.id): raise HTTPException(400, "Вы уже друзья")
    rev = db.scalar(select(Friendship).where(Friendship.requester_id == x.id, Friendship.addressee_id == u.id, Friendship.status == "pending"))
    if rev:
        rev.status = "accepted"; notify(db, x.id, "friend_accept", f"{u.name} принял(а) вашу заявку в друзья", u, "user", u.id); db.commit()
        return {"status": "accepted"}
    if db.scalar(select(Friendship.id).where(Friendship.requester_id == u.id, Friendship.addressee_id == x.id)):
        raise HTTPException(400, "Заявка уже отправлена")
    db.add(Friendship(requester_id=u.id, addressee_id=x.id))
    notify(db, x.id, "friend_request", f"{u.name} хочет добавить вас в друзья", u, "user", u.id)
    db.commit()
    return {"status": "pending"}


@router.post("/friends/requests/{rid}/accept")
def accept_request(rid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    fr = db.get(Friendship, rid)
    if not fr or fr.addressee_id != u.id or fr.status != "pending": raise HTTPException(404, "Заявка не найдена")
    if blocked_between(db, u.id, fr.requester_id): raise HTTPException(404, "Заявка не найдена")
    fr.status = "accepted"
    notify(db, fr.requester_id, "friend_accept", f"{u.name} принял(а) вашу заявку в друзья", u, "user", u.id)
    db.commit()
    return {"ok": True}


@router.post("/friends/requests/{rid}/decline", status_code=204)
def decline_request(rid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    fr = db.get(Friendship, rid)
    if not fr or fr.status != "pending" or u.id not in (fr.addressee_id, fr.requester_id): raise HTTPException(404, "Заявка не найдена")
    db.delete(fr); db.commit()
    return Response(status_code=204)


@router.delete("/friends/{uid}", status_code=204)
def unfriend(uid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    db.execute(delete(Friendship).where(or_(and_(Friendship.requester_id == u.id, Friendship.addressee_id == uid), and_(Friendship.requester_id == uid, Friendship.addressee_id == u.id))))
    db.commit()
    return Response(status_code=204)


@router.get("/friends/mutual/{uid}")
def mutual_friends(uid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    x = visible_user(db, u, uid)
    return [card(y) for y in users_by_ids(db, friend_ids(db, u.id) & friend_ids(db, x.id), u)]


@router.post("/follows", status_code=201)
def follow(r: UserRef, u: User = Depends(need_user), db: Session = Depends(get_db)):
    if r.user_id == u.id: raise HTTPException(400, "Нельзя подписаться на себя")
    x = visible_user(db, u, r.user_id)
    if not x.is_profile_public: raise HTTPException(403, "Профиль закрыт — отправьте заявку в друзья")
    if db.scalar(select(Follow.id).where(Follow.follower_id == u.id, Follow.followee_id == x.id)): raise HTTPException(400, "Вы уже подписаны")
    db.add(Follow(follower_id=u.id, followee_id=x.id))
    notify(db, x.id, "follow", f"{u.name} подписался(лась) на вас", u, "user", u.id)
    db.commit()
    return {"ok": True}


@router.delete("/follows/{uid}", status_code=204)
def unfollow(uid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    db.execute(delete(Follow).where(Follow.follower_id == u.id, Follow.followee_id == uid)); db.commit()
    return Response(status_code=204)


# ---------- блокировки (взаимная невидимость) ----------
@router.get("/blocks")
def my_blocks(u: User = Depends(need_user), db: Session = Depends(get_db)):
    ids = [r[0] for r in db.execute(select(Block.blocked_id).where(Block.blocker_id == u.id))]
    return [card(x) for x in db.scalars(select(User).where(User.id.in_(ids)))] if ids else []


@router.post("/blocks", status_code=201)
def block(r: UserRef, u: User = Depends(need_user), db: Session = Depends(get_db)):
    x = db.get(User, r.user_id)
    if not x or x.id == u.id: raise HTTPException(400, "Нельзя заблокировать этого пользователя")
    if is_staff(x): raise HTTPException(400, "Нельзя заблокировать модератора или администратора")
    if not db.scalar(select(Block.id).where(Block.blocker_id == u.id, Block.blocked_id == x.id)):
        db.add(Block(blocker_id=u.id, blocked_id=x.id))
    pair = lambda M, a, b: and_(getattr(M, a) == u.id, getattr(M, b) == x.id)
    db.execute(delete(Friendship).where(or_(pair(Friendship, "requester_id", "addressee_id"), and_(Friendship.requester_id == x.id, Friendship.addressee_id == u.id))))
    db.execute(delete(Follow).where(or_(pair(Follow, "follower_id", "followee_id"), and_(Follow.follower_id == x.id, Follow.followee_id == u.id))))
    db.commit()
    return {"ok": True}


@router.delete("/blocks/{uid}", status_code=204)
def unblock(uid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    db.execute(delete(Block).where(Block.blocker_id == u.id, Block.blocked_id == uid)); db.commit()
    return Response(status_code=204)


# ---------- профиль другого пользователя ----------
@router.get("/users/{uid}")
def user_profile(uid: int, viewer: User = Depends(need_user), db: Session = Depends(get_db)):
    x = visible_user(db, viewer, uid)
    mine = viewer.id == x.id
    rel = None if mine else relation(db, viewer, x.id)
    base = dict(card(x), is_me=mine, relation=rel, banned=False)
    if not (mine or x.is_profile_public or (rel and rel["friend"])):
        return dict(base, restricted=True)            # закрытый профиль: только имя и аватар
    pls = db.scalars(select(Playlist).where(Playlist.owner_id == x.id, Playlist.removed_at.is_(None), *([] if mine else [Playlist.is_public.is_(True)])).order_by(Playlist.id.desc())).all()
    return dict(base, restricted=False, about=x.about, music_interests=x.music_interests, favorite_artists=x.favorite_artists,
                friends_count=len(friend_ids(db, x.id)),
                followers_count=db.scalar(select(func.count()).select_from(Follow).where(Follow.followee_id == x.id)),
                playlists=[p_out(db, p, viewer=viewer) for p in pls], created_at=iso(x.created_at))


@router.get("/users/{uid}/posts")
def user_posts(uid: int, viewer: User = Depends(need_user), db: Session = Depends(get_db)):
    x = visible_user(db, viewer, uid)
    posts = db.scalars(select(Post).where(Post.author_id == x.id, Post.removed_at.is_(None)).order_by(Post.id.desc()).limit(50)).all()
    return with_meta(db, [post_out(db, p, viewer) for p in posts if can_view_post(db, viewer, p)], viewer)


# ---------- посты ----------
def post_out(db: Session, p: Post, viewer: User | None, reason: str | None = None) -> dict:
    a = db.get(User, p.author_id)
    imgs = db.scalars(select(PostImage).where(PostImage.post_id == p.id).order_by(PostImage.position)).all()
    trs = db.scalars(select(Track).join(PostTrack, PostTrack.track_id == Track.id).where(PostTrack.post_id == p.id, Track.removed_at.is_(None)).order_by(PostTrack.position)).all()
    return dict(id=p.id, author=card(a), text=p.text, visibility=p.visibility, created_at=iso(p.created_at), reason=reason,
                is_mine=bool(viewer and viewer.id == p.author_id), images=[i.file_name for i in imgs], tracks=[t_out(t) for t in trs])


REACTIONS = ("like", "love", "fire", "laugh", "wow", "sad")


def with_meta(db: Session, outs: list[dict], viewer: User | None) -> list[dict]:
    """Добавляет к постам реакции и число комментариев: три запроса на весь список (а не по запросу на пост)."""
    ids = [o["id"] for o in outs]
    meta = {i: dict(reactions={}, my_reaction=None, comments_count=0) for i in ids}
    if ids:
        for pid, kind, n in db.execute(select(PostReaction.post_id, PostReaction.kind, func.count()).where(PostReaction.post_id.in_(ids)).group_by(PostReaction.post_id, PostReaction.kind)):
            meta[pid]["reactions"][kind] = n
        for pid, n in db.execute(select(PostComment.post_id, func.count()).where(PostComment.post_id.in_(ids), PostComment.removed_at.is_(None)).group_by(PostComment.post_id)):
            meta[pid]["comments_count"] = n
        if viewer:
            for pid, kind in db.execute(select(PostReaction.post_id, PostReaction.kind).where(PostReaction.post_id.in_(ids), PostReaction.user_id == viewer.id)):
                meta[pid]["my_reaction"] = kind
    for o in outs: o.update(meta[o["id"]])
    return outs


def visible_post(db: Session, u: User, pid: int) -> Post:
    """Пост, который пользователь вправе видеть (иначе 404/403) — основа проверок для реакций и комментариев."""
    p = db.get(Post, pid)
    if not p or p.removed_at: raise HTTPException(404, "Пост не найден")
    if blocked_between(db, u.id, p.author_id): raise HTTPException(404, "Пост не найден")
    if not can_view_post(db, u, p): raise HTTPException(403, "Пост недоступен")
    return p


@router.post("/posts", status_code=201)
async def create_post(text: str = Form("", max_length=2000), visibility: str | None = Form(None), track_ids: str = Form(""),
                      images: list[UploadFile] = File(default=[]), u: User = Depends(need_user), db: Session = Depends(get_db)):
    imgs = [f for f in images if f and f.filename]
    if len(imgs) > 8: raise HTTPException(400, "Не больше 8 изображений")
    try: tids = [int(x) for x in track_ids.split(",") if x.strip()]
    except ValueError: raise HTTPException(400, "Некорректный список треков")
    if len(set(tids)) > 10: raise HTTPException(400, "Не больше 10 треков")
    tracks = [get_track(db, t, u, write=True) for t in dict.fromkeys(tids)]       # прикреплять можно только свои треки
    if not text.strip() and not imgs and not tracks: raise HTTPException(400, "Пост пуст: добавьте текст, изображение или трек")
    vis = visibility if visibility in ("friends", "public") else ("public" if u.is_profile_public else "friends")
    if vis == "public" and not u.is_profile_public: vis = "friends"                # закрытый профиль публикует только друзьям
    p = Post(author_id=u.id, text=text.strip(), visibility=vis)
    db.add(p); db.flush()
    saved: list[str] = []
    try:
        for n, f in enumerate(imgs):
            url = await save_image(f, f"post-{uuid.uuid4().hex}")
            saved.append(os.path.basename(url)); db.add(PostImage(post_id=p.id, file_name=saved[-1], position=n))
        for n, t in enumerate(tracks): db.add(PostTrack(post_id=p.id, track_id=t.id, position=n))
        db.commit()
    except Exception:
        db.rollback()
        for fn in saved: remove_file(fn)
        raise
    log_event(db, "post", u.id)
    return with_meta(db, [post_out(db, p, u)], u)[0]


@router.get("/posts/{pid}")
def one_post(pid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    return with_meta(db, [post_out(db, visible_post(db, u, pid), u)], u)[0]


# ---------- реакции и комментарии к постам ----------
class ReactionIn(BaseModel):
    kind: Literal["like", "love", "fire", "laugh", "wow", "sad"]


class PostCommentIn(Strip):
    text: str = Field(min_length=1, max_length=1000)


def reaction_state(db: Session, pid: int, u: User) -> dict:
    return {k: v for k, v in with_meta(db, [dict(id=pid)], u)[0].items() if k != "id"}


@router.put("/posts/{pid}/reaction")
def set_reaction(pid: int, r: ReactionIn, u: User = Depends(need_user), db: Session = Depends(get_db)):
    """Поставить реакцию или заменить свою прежнюю (у пользователя одна реакция на пост)."""
    visible_post(db, u, pid)
    for attempt in (1, 2):
        cur = db.scalar(select(PostReaction).where(PostReaction.post_id == pid, PostReaction.user_id == u.id))
        if cur: cur.kind = r.kind
        else: db.add(PostReaction(post_id=pid, user_id=u.id, kind=r.kind))
        try: db.commit(); break
        except IntegrityError:                          # двойной тап: параллельный запрос уже создал реакцию — обновим её
            db.rollback()
            if attempt == 2: raise
    return reaction_state(db, pid, u)


@router.delete("/posts/{pid}/reaction")
def clear_reaction(pid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    visible_post(db, u, pid)
    db.execute(delete(PostReaction).where(PostReaction.post_id == pid, PostReaction.user_id == u.id)); db.commit()
    return reaction_state(db, pid, u)


def pc_out(c: PostComment, a: User, viewer: User, post_author_id: int) -> dict:
    return dict(id=c.id, author=card(a), text=c.text, created_at=iso(c.created_at),
                mine=c.author_id == viewer.id, can_delete=viewer.id in (c.author_id, post_author_id))


@router.get("/posts/{pid}/comments")
def post_comments(pid: int, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), u: User = Depends(need_user), db: Session = Depends(get_db)):
    p = visible_post(db, u, pid)
    hid = blocked_ids(db, u.id)
    q = (select(PostComment, User).join(User, User.id == PostComment.author_id)
         .where(PostComment.post_id == pid, PostComment.removed_at.is_(None), User.deleted_at.is_(None), User.is_banned.is_(False)))
    if hid: q = q.where(PostComment.author_id.not_in(hid))            # комментарии заблокированных не показываем (взаимная невидимость)
    rows = db.execute(q.order_by(PostComment.id).offset(offset).limit(limit + 1)).all()
    return dict(items=[pc_out(c, a, u, p.author_id) for c, a in rows[:limit]], has_more=len(rows) > limit)


@router.post("/posts/{pid}/comments", status_code=201)
def add_post_comment(pid: int, c: PostCommentIn, u: User = Depends(need_user), db: Session = Depends(get_db)):
    p = visible_post(db, u, pid)
    cm = PostComment(post_id=pid, author_id=u.id, text=c.text)
    db.add(cm)
    if p.author_id != u.id: notify(db, p.author_id, "post_comment", f"{u.name} прокомментировал(а) ваш пост", u, "post", pid)
    db.commit()
    return pc_out(cm, u, u, p.author_id)


@router.delete("/posts/{pid}/comments/{cid}", status_code=204)
def del_post_comment(pid: int, cid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    """Удалить комментарий может его автор или автор поста."""
    p, cm = db.get(Post, pid), db.get(PostComment, cid)
    if not p or p.removed_at or not cm or cm.post_id != pid or cm.removed_at: raise HTTPException(404, "Комментарий не найден")
    if u.id not in (cm.author_id, p.author_id): raise HTTPException(403, "Нет прав")
    db.delete(cm); db.commit()
    return Response(status_code=204)


@router.delete("/posts/{pid}", status_code=204)
def delete_post(pid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    p = db.get(Post, pid)
    if not p or p.removed_at: raise HTTPException(404, "Пост не найден")
    if p.author_id != u.id: raise HTTPException(403, "Только автор может удалить пост")
    names = [i.file_name for i in db.scalars(select(PostImage).where(PostImage.post_id == pid))]
    db.delete(p); db.commit()
    for n in names: remove_file(n)
    return Response(status_code=204)


# ---------- рекомендации по вкусу ----------
def taste_of(db: Session, uid: int, public_only: bool) -> dict[str, set[str]]:
    """Вкус: жанры/настроения/исполнители. Для чужих — только по трекам из их публичных плейлистов."""
    q = select(Track.genre, Track.mood, Track.artist).where(Track.removed_at.is_(None))
    if public_only:
        q = q.where(Track.id.in_(select(PlaylistItem.track_id).join(Playlist, Playlist.id == PlaylistItem.playlist_id)
                                 .where(Playlist.owner_id == uid, Playlist.is_public.is_(True), Playlist.removed_at.is_(None))))
    else:
        q = q.where(Track.owner_id == uid)
    out = {"genres": set(), "moods": set(), "artists": set()}
    for g, m_, a in db.execute(q):
        if g: out["genres"].add(g.casefold())
        if m_: out["moods"].add(m_.casefold())
        if a and a != UNKNOWN_ARTIST: out["artists"].add(a.casefold())
    return out


def similar_users(db: Session, u: User, limit: int = 20) -> list[dict]:
    """Только если пользователь включил рекомендации и они не выключены платформой. Закрытые профили исключены."""
    if not (u.taste_recs and get_setting(db, "recommendations_enabled")): return []
    mine = taste_of(db, u.id, False)
    if not any(mine.values()): return []
    skip = blocked_ids(db, u.id) | friend_ids(db, u.id) | {u.id}
    res = []
    for x in db.scalars(select(User).where(User.is_profile_public.is_(True), User.deleted_at.is_(None), User.is_banned.is_(False), User.role == "user")):
        if x.id in skip: continue
        theirs = taste_of(db, x.id, True); sims, shared = [], {}
        for k in mine:
            union = mine[k] | theirs[k]
            if union: sims.append(len(mine[k] & theirs[k]) / len(union))
            shared[k] = sorted(mine[k] & theirs[k])
        score = sum(sims) / 3 if sims else 0
        if score >= 0.1: res.append(dict(user=card(x), score=round(score, 2), shared=shared))
    return sorted(res, key=lambda r: -r["score"])[:limit]


@router.get("/recommendations/users")
def recommended_users(u: User = Depends(need_user), db: Session = Depends(get_db)):
    return dict(enabled=u.taste_recs and get_setting(db, "recommendations_enabled"), platform_enabled=get_setting(db, "recommendations_enabled"),
                items=similar_users(db, u))


@router.get("/feed")
def feed(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50), u: User = Depends(need_user), db: Session = Depends(get_db)):
    """Посты друзей и подписок (+ свои). При включённой настройке — публичные посты людей со схожим вкусом."""
    fr = friend_ids(db, u.id)
    fol = {r[0] for r in db.execute(select(Follow.followee_id).where(Follow.follower_id == u.id))}
    sim = {r["user"]["id"] for r in similar_users(db, u, 40)}
    hid = blocked_ids(db, u.id)
    authors = (fr | fol | sim | {u.id}) - hid
    if not authors: return dict(items=[], has_more=False)
    rows = db.execute(select(Post, User).join(User, User.id == Post.author_id).where(
        Post.author_id.in_(authors), Post.removed_at.is_(None), User.deleted_at.is_(None), User.is_banned.is_(False)).order_by(Post.id.desc()).limit(300)).all()
    items = []
    for p, a in rows:
        if a.id == u.id: why = "mine"
        elif a.id in fr: why = "friend"
        elif p.visibility == "public" and a.is_profile_public and a.id in fol: why = "follow"
        elif p.visibility == "public" and a.is_profile_public and a.id in sim: why = "similar"
        else: continue
        items.append(post_out(db, p, u, why))
    return dict(items=with_meta(db, items[offset:offset + limit], u), has_more=len(items) > offset + limit)


# ---------- уведомления ----------
def n_out(n: Notification) -> dict:
    return dict(id=n.id, kind=n.kind, text=n.text, ref_type=n.ref_type, ref_id=n.ref_id, from_staff=n.from_staff, read=n.read_at is not None, created_at=iso(n.created_at))


@router.get("/notifications")
def notifications(unread_only: bool = False, u: User = Depends(need_user), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == u.id)
    if unread_only: q = q.where(Notification.read_at.is_(None))
    return [n_out(n) for n in db.scalars(q.order_by(Notification.id.desc()).limit(100))]


@router.post("/notifications/read-all", status_code=204)
def read_all(u: User = Depends(need_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == u.id, Notification.read_at.is_(None))): n.read_at = now()
    db.commit()
    return Response(status_code=204)


@router.post("/notifications/{nid}/read", status_code=204)
def read_one(nid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if not n or n.user_id != u.id: raise HTTPException(404, "Уведомление не найдено")
    n.read_at = n.read_at or now(); db.commit()
    return Response(status_code=204)


# ---------- глобальный поиск ----------
@router.get("/search")
def search(q: str = Query("", max_length=100), type: str = Query("all", pattern="^(all|users|posts|playlists|tracks)$"),
           u: User = Depends(need_user), db: Session = Depends(get_db)):
    """Закрытые профили не попадают в выдачу; чужие приватные плейлисты/треки/посты — тоже."""
    q = q.strip()
    if len(q) < 2: return dict(users=[], posts=[], playlists=[], tracks=[])
    hid = blocked_ids(db, u.id)
    ok_author = and_(User.is_profile_public.is_(True), User.deleted_at.is_(None), User.is_banned.is_(False))
    res = dict(users=[], posts=[], playlists=[], tracks=[])
    if type in ("all", "users") and get_setting(db, "search_users_enabled"):
        res["users"] = [card(x) for x in db.scalars(select(User).where(ok_author, User.id != u.id).order_by(User.name).limit(300)) if x.id not in hid and like(q, x.name, x.nickname)][:20]
    if type in ("all", "posts"):
        rows = db.execute(select(Post, User).join(User, User.id == Post.author_id).where(ok_author, Post.visibility == "public", Post.removed_at.is_(None)).order_by(Post.id.desc()).limit(400)).all()
        res["posts"] = with_meta(db, [post_out(db, p, u) for p, a in rows if a.id not in hid and like(q, p.text)][:20], u)
    if type in ("all", "playlists"):
        rows = db.execute(select(Playlist, User).join(User, User.id == Playlist.owner_id).where(ok_author, Playlist.is_public.is_(True), Playlist.removed_at.is_(None)).order_by(Playlist.id.desc()).limit(400)).all()
        res["playlists"] = [p_out(db, p, viewer=u) for p, a in rows if a.id not in hid and like(q, p.name, p.description)][:20]
    if type in ("all", "tracks"):
        rows = db.execute(select(Track, User).join(PlaylistItem, PlaylistItem.track_id == Track.id).join(Playlist, Playlist.id == PlaylistItem.playlist_id)
                          .join(User, User.id == Playlist.owner_id).where(ok_author, Playlist.is_public.is_(True), Playlist.removed_at.is_(None), Track.removed_at.is_(None)).limit(600)).all()
        seen, out = set(), []
        for t, a in rows:
            if t.id in seen or a.id in hid or not like(q, t.title, t.artist, t.genre, t.mood): continue
            seen.add(t.id); out.append(dict(t_out(t), owner_name=a.name))
        res["tracks"] = out[:20]
    return res


# ---------- жалобы пользователей и обращения в поддержку ----------
class ReportIn(Strip):
    content_type: Literal["post", "comment", "playlist", "track", "profile", "support"]
    content_id: int | None = None
    category: Literal["spam", "abuse", "violence", "illegal", "copyright", "other"] = "other"
    reason: str = Field("", max_length=2000)


def resolve_target(db: Session, u: User, kind: str, cid: int | None) -> int | None:
    """Проверяет, что автор жалобы вообще видит объект (так нельзя «пробить» закрытый контент). Возвращает id автора."""
    if kind == "support": return None
    if cid is None: raise HTTPException(422, "Не указан объект жалобы")
    if kind == "post":
        p = db.get(Post, cid)
        if not p or not can_view_post(db, u, p): raise HTTPException(404, "Объект не найден")
        return p.author_id
    if kind == "comment":
        c = db.get(Comment, cid)
        if not c or c.removed_at: raise HTTPException(404, "Объект не найден")
        get_track(db, c.track_id, u); return c.author_id
    if kind == "playlist": return get_playlist(db, cid, u).owner_id
    if kind == "track": return get_track(db, cid, u).owner_id
    return visible_user(db, u, cid).id


@router.post("/reports", status_code=201)
def create_report(r: ReportIn, u: User = Depends(need_user), db: Session = Depends(get_db)):
    if r.content_type == "support" and not r.reason: raise HTTPException(422, "Опишите проблему")
    target = resolve_target(db, u, r.content_type, r.content_id)
    if target == u.id: raise HTTPException(400, "Нельзя пожаловаться на свой контент")
    if r.content_type != "support" and db.scalar(select(Report.id).where(Report.reporter_id == u.id, Report.content_type == r.content_type,
                                                                          Report.content_id == r.content_id, Report.status != "closed")):
        raise HTTPException(400, "Вы уже отправили жалобу на этот объект")
    same = db.scalar(select(func.count()).select_from(Report).where(Report.content_type == r.content_type, Report.content_id == r.content_id, Report.status != "closed")) or 0
    prio = "high" if (r.category in ("violence", "illegal") or same >= 2) else ("low" if r.category == "spam" else "normal")
    rep = Report(reporter_id=u.id, content_type=r.content_type, content_id=r.content_id, target_user_id=target, category=r.category, reason=r.reason, priority=prio)
    db.add(rep)
    if r.content_type != "support" and same >= 2:      # повышаем приоритет остальных открытых жалоб на тот же объект
        for o in db.scalars(select(Report).where(Report.content_type == r.content_type, Report.content_id == r.content_id, Report.status != "closed")): o.priority = "high"
    db.commit()
    return {"id": rep.id, "status": rep.status}


@router.get("/reports/mine")
def my_reports(u: User = Depends(need_user), db: Session = Depends(get_db)):
    return [dict(id=r.id, content_type=r.content_type, category=r.category, status=r.status, resolution=r.resolution, created_at=iso(r.created_at))
            for r in db.scalars(select(Report).where(Report.reporter_id == u.id).order_by(Report.id.desc()).limit(50))]


# ---------- соавторские плейлисты ----------
class MemberIn(BaseModel):
    user_id: int
    role: Literal["editor", "viewer"] = "editor"


class RoleIn(BaseModel):
    role: Literal["editor", "viewer"]


class RespondIn(BaseModel):
    accept: bool


def member_out(m: PlaylistMember, x: User) -> dict:
    return dict(user=card(x), role=m.role, status=m.status)


@router.get("/playlists/{pid}/members")
def playlist_members(pid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    pl = get_playlist(db, pid, u)
    rows = db.execute(select(PlaylistMember, User).join(User, User.id == PlaylistMember.user_id).where(PlaylistMember.playlist_id == pid)).all()
    owner_view = pl.owner_id == u.id
    return dict(owner=card(db.get(User, pl.owner_id)), members=[member_out(m, x) for m, x in rows if (owner_view or m.status == "accepted" or m.user_id == u.id) and is_active_user(x)])


@router.post("/playlists/{pid}/members", status_code=201)
def invite_member(pid: int, i: MemberIn, u: User = Depends(need_user), db: Session = Depends(get_db)):
    pl = get_playlist(db, pid, u, write=True)
    if i.user_id == u.id: raise HTTPException(400, "Вы уже владелец")
    if not are_friends(db, u.id, i.user_id): raise HTTPException(403, "Соавторами можно приглашать только друзей")
    if db.scalar(select(PlaylistMember.id).where(PlaylistMember.playlist_id == pid, PlaylistMember.user_id == i.user_id)): raise HTTPException(400, "Уже приглашён")
    db.add(PlaylistMember(playlist_id=pid, user_id=i.user_id, role=i.role, invited_by=u.id))
    notify(db, i.user_id, "collab_invite", f"{u.name} приглашает вас в плейлист «{pl.name}» ({'соавтор' if i.role == 'editor' else 'слушатель'})", u, "playlist", pid)
    db.commit()
    return {"ok": True}


@router.patch("/playlists/{pid}/members/{uid}")
def change_member(pid: int, uid: int, r: RoleIn, u: User = Depends(need_user), db: Session = Depends(get_db)):
    get_playlist(db, pid, u, write=True)
    m = db.scalar(select(PlaylistMember).where(PlaylistMember.playlist_id == pid, PlaylistMember.user_id == uid))
    if not m: raise HTTPException(404, "Участник не найден")
    m.role = r.role; db.commit()
    return {"ok": True}


@router.delete("/playlists/{pid}/members/{uid}", status_code=204)
def remove_member(pid: int, uid: int, u: User = Depends(need_user), db: Session = Depends(get_db)):
    pl = db.get(Playlist, pid)
    if not pl or pl.removed_at: raise HTTPException(404, "Плейлист не найден")
    if u.id not in (pl.owner_id, uid): raise HTTPException(403, "Нет прав")        # владелец или сам участник (выйти)
    db.execute(delete(PlaylistMember).where(PlaylistMember.playlist_id == pid, PlaylistMember.user_id == uid)); db.commit()
    return Response(status_code=204)


@router.post("/playlists/{pid}/members/respond")
def respond_invite(pid: int, r: RespondIn, u: User = Depends(need_user), db: Session = Depends(get_db)):
    m = db.scalar(select(PlaylistMember).where(PlaylistMember.playlist_id == pid, PlaylistMember.user_id == u.id, PlaylistMember.status == "pending"))
    pl = db.get(Playlist, pid)
    if not m or not pl: raise HTTPException(404, "Приглашение не найдено")
    if r.accept: m.status = "accepted"
    else: db.delete(m)
    notify(db, pl.owner_id, "collab_reply", f"{u.name} {'принял(а)' if r.accept else 'отклонил(а)'} приглашение в «{pl.name}»", u, "playlist", pid)
    db.commit()
    return {"ok": True}


@router.get("/invites/playlists")
def my_invites(u: User = Depends(need_user), db: Session = Depends(get_db)):
    rows = db.execute(select(PlaylistMember, Playlist).join(Playlist, Playlist.id == PlaylistMember.playlist_id)
                      .where(PlaylistMember.user_id == u.id, PlaylistMember.status == "pending", Playlist.removed_at.is_(None))).all()
    return [dict(playlist_id=p.id, name=p.name, role=m.role, invited_by=card(db.get(User, m.invited_by))) for m, p in rows]


# ---------- статистика (события) ----------
class VisitIn(BaseModel):
    visitor_id: str = Field("", max_length=40)


@router.post("/stats/visit", status_code=204)
def stats_visit(v: VisitIn, u: User | None = Depends(opt_user), db: Session = Depends(get_db)):
    log_event(db, "visit", u.id if u else None, v.visitor_id)
    return Response(status_code=204)


@router.post("/tracks/{tid}/listen", status_code=204)
def stats_listen(tid: int, u: User | None = Depends(opt_user), db: Session = Depends(get_db)):
    get_track(db, tid, u)
    log_event(db, "listen", u.id if u else None)
    return Response(status_code=204)
