"""Slash commands (/new /sessions /stop /close /model /status /help) and opening a session from Discord."""
from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass, field

import httpx

from . import fmt
from .discord_api import DiscordError
from .harness import HarnessError
from .texts import T
from .tools import ToolsMixin, focused_option

log = logging.getLogger("harness_discord.commands")

HERR = (HarnessError, httpx.HTTPError)
MENU_MAX = 25
FALLBACK_MODELS = [("claude-opus-5-5", "Opus 5.5"), ("claude-sonnet-5-5", "Sonnet 5.5"), ("claude-haiku-4-5-20251001", "Haiku 4.5")]
CTX_SEND = {T[k]["ctx_send"] for k in T}
CTX_NEW = {T[k]["ctx_new"] for k in T}


def resolve_model(arg: str, models: list[tuple[str, str]]) -> str | None:
    """`/model <arg>` -> a model id ("" = default), or None when it matches nothing. Accepts an id, a label
    ("Opus 5.5", any case) or a family alias ("opus", "sonnet", "haiku": the newest known), as on Telegram."""
    a = " ".join((arg or "").split()).lower()
    if a in ("default", "-", "auto", "", "par défaut"):
        return ""
    for mid, label in models:
        if a in (mid.lower(), label.lower(), label.lower().replace(" ", "-")):
            return mid
    fam = [mid for mid, _ in models if a.isalpha() and f"-{a}-" in f"-{mid.lower()}-"]
    return fam[0] if fam else None


def resolve_topic(arg: str, topics: list[dict]) -> str | None:
    """A topic typed or chosen for /new: its path, `project/topic`, or a topic name / part of it (when only one fits)."""
    a = " ".join((arg or "").split()).lower()
    if not a:
        return None
    for x in topics:
        if a in (x["path"].lower(), f"{x['project']}/{x['name']}".lower()):
            return x["path"]
    for hit in ([x for x in topics if x["name"].lower() == a], [x for x in topics if a in f"{x['project']}/{x['name']}".lower()]):
        if len(hit) == 1:
            return hit[0]["path"]
    return None


@dataclass
class Pending:
    text: str
    files: list[dict]
    channel: str                       # channel the request came from (DM, main channel or a thread made by hand)
    message: str                       # id of the user's message ("" for /new)
    in_dm: bool
    thread: str = ""                   # a thread made by hand: it becomes the session's thread
    title: str = ""
    project: str = ""                 # restricts the topic menu to that project
    at: float = field(default_factory=time.time)



# French names of commands, groups, sub-commands and options (Discord: lowercase, no space, 1-32 characters). The name the
# bot receives is always the English one; these only change what a French Discord shows.
NAMES_FR = {"new": "nouvelle", "close": "fermer", "model": "modèle", "status": "statut", "help": "aide", "tasks": "tâches",
            "task": "tâche", "add": "ajouter", "list": "liste", "run": "lancer", "toggle": "basculer", "health": "santé",
            "search": "chercher", "backup": "sauvegarde", "queue": "file", "files": "fichiers", "ls": "liste", "get": "envoyer",
            "unread": "nonlus", "outbox": "envois", "title": "titre", "form": "formulaire", "text": "texte", "query": "requête",
            "path": "chemin"}
NAME_OK = re.compile(r"^[-_\w]{1,32}$", re.UNICODE)


class CommandsMixin(ToolsMixin):
    """Slash commands for `Bot` (uses its h, rest, gw, store, lay, followers, pending, t, err, menu, where, topics, start...)."""

    def d(self, key: str) -> dict:
        """Description in the bot's language, plus the other language as a localization (Discord shows it to its users)."""
        other = "en-US" if self.lang == "fr" else "fr"
        src = T["en" if other == "en-US" else "fr"]
        out = {"description": self.t(key)[:100]}
        if key in src and src[key][:100] != out["description"]:
            out["description_localizations"] = {other: src[key][:100]}
        return out

    def n(self, name: str) -> dict:
        fr = NAMES_FR.get(name)
        return {"name": name, **({"name_localizations": {"fr": fr}} if fr and fr != name else {})}

    def opt(self, name: str, key: str, kind: int = 3, **extra) -> dict:
        return {"type": kind, **self.n(name), **self.d(key), **extra}

    def sub(self, name: str, key: str, options: list | None = None, kind: int = 1) -> dict:
        out = {"type": kind, **self.n(name), **self.d(key)}
        if options:
            out["options"] = options
        return out

    def choice(self, value: str, key: str) -> dict:
        out = {"name": self.t(key)[:100], "value": value}
        if self.lang != "fr" and T["fr"].get(key) and T["fr"][key] != out["name"]:
            out["name_localizations"] = {"fr": T["fr"][key][:100]}
        elif self.lang == "fr" and T["en"].get(key) and T["en"][key] != out["name"]:
            out["name_localizations"] = {"en-US": T["en"][key][:100]}
        return out

    def ctx(self, key: str) -> dict:
        """A message context menu: its name is shown as is (capitals and spaces allowed, 32 characters at most)."""
        out = {"name": self.t(key)[:32], "type": 3}
        other, code = ("en", "en-US") if self.lang == "fr" else ("fr", "fr")
        if T[other][key][:32] != out["name"]:
            out["name_localizations"] = {code: T[other][key][:32]}
        return out

    async def commands(self) -> list[dict]:
        sub, opt, grp = self.sub, self.opt, lambda name, key, subs: self.sub(name, key, subs, kind=2)
        job = lambda: opt("job", "d_job", required=True, autocomplete=True)    # noqa: E731
        return [
            {**self.n("new"), **self.d("d_new"), "options": [
                opt("title", "d_title"), opt("topic", "d_newtopic", autocomplete=True), opt("form", "d_newform", 5)]},
            {**self.n("sessions"), **self.d("d_sessions")},
            {**self.n("stop"), **self.d("d_stop")},
            {**self.n("close"), **self.d("d_close")},
            {**self.n("model"), **self.d("d_model"), "options": [opt("id", "d_modelid", autocomplete=True)]},
            {**self.n("status"), **self.d("d_status")},
            {**self.n("help"), **self.d("d_help")},
            {**self.n("harness"), **self.d("d_harness"), "options": [
                sub("tasks", "d_tasks", [opt("status", "d_status_f", choices=[self.choice(v, "f_" + v) for v in ("open", "todo", "doing", "done", "all")])]),
                grp("task", "d_task", [sub("add", "d_task_add")]),
                grp("cron", "d_cron", [sub("list", "d_cron_list"), sub("run", "d_cron_run", [job()]), sub("toggle", "d_cron_toggle", [job()])]),
                grp("agent", "d_agent", [sub("run", "d_agent_run", [opt("agent", "d_agentopt", required=True, autocomplete=True), opt("text", "d_agent_text")])]),
                sub("usage", "d_usage"), sub("health", "d_health"),
                sub("search", "d_search", [opt("query", "d_query", required=True)]),
                grp("backup", "d_backup", [sub("status", "d_backup_status"), sub("run", "d_backup_run")]),
                sub("infra", "d_infra"), sub("queue", "d_queue"),
                grp("files", "d_files", [sub("ls", "d_files_ls", [opt("path", "d_path")]), sub("get", "d_files_get", [opt("path", "d_path", required=True)])]),
                grp("mail", "d_mail", [sub("unread", "d_mail_unread"), sub("outbox", "d_mail_outbox")]),
                sub("audit", "d_audit")]},
            self.ctx("ctx_send"),                                 # message context menus (no description allowed)
            self.ctx("ctx_new"),
        ]

    async def on_command(self, i: dict) -> None:
        name = i["data"]["name"]
        if i["data"].get("type") == 3:
            return await self.cmd_context(i, name)
        opts = {o["name"]: o.get("value") for o in i["data"].get("options", [])}
        ch, in_dm = i["channel_id"], not i.get("guild_id")
        if name == "help":
            return await self.rest.respond(i, 4, self.t("help"), ephemeral=True)
        if name == "harness":
            return await self.cmd_harness(i)
        if name in ("new", "sessions") and not self.allowed_here(ch, in_dm):
            return await self.rest.respond(i, 4, self.t("refused_here"), ephemeral=True)
        if name == "new" and opts.get("form"):
            return await self.modal_new(i)
        if name == "new":
            return await self.cmd_new(i, ch, in_dm, str(opts.get("title") or "")[:100], str(opts.get("topic") or ""))
        await self.rest.respond(i, 5, ephemeral=True)               # deferred: Harness may take a moment
        reply = lambda s: self.rest.followup(i, self.gw.app_id, s, ephemeral=True)    # noqa: E731
        try:
            if name == "sessions":
                return await self.cmd_sessions(i, reply)
            sid = self.current_session(i)
            if not sid:
                return await reply(self.t("no_session"))
            if name == "stop":
                await self.h.stop(sid)
                self.done("stop", sid)
                f = self.followers.get(sid)
                if f:
                    f.expect = 0
                return await reply(self.t("stopped"))
            if name == "close":
                await self.h.archive(sid)
                self.done("close", sid)
                if self.store.channel_of(sid):
                    c = self.store.channel_of(sid)
                    self.store.unlink(sid)
                    await reply(self.t("closed"))
                    return await self.rest.close_thread(c)                 # locked and archived, still readable
                if self.store.data.get("dm_current") == sid:
                    self.store.set_dm("")
                return await reply(self.t("closed"))
            if name == "model":
                if opts.get("id"):
                    arg = str(opts["id"]).strip()
                    models = await self.model_list()
                    model = resolve_model(arg, models)
                    if model is None and arg.startswith("claude-"):
                        model = arg                                  # a full id Harness does not list: Harness decides
                    if model is None:
                        return await reply(self.t("model_unknown", model=arg[:60], choices=", ".join(label for _, label in models)))
                    s = await self.h.set_model(sid, model)
                    self.done("model", f"{sid} {model}")
                    return await reply(self.t("model_set", model=s.get("model") or self.t("default")))
                s = await self.h.session(sid)
                return await reply(self.t("model_now", model=s.get("real_model") or s.get("model") or self.t("default")))
            if name == "status":
                s = await self.h.session(sid)
                return await reply(self.t("status", title=s.get("title") or "…", path=s["path"].split("projects/", 1)[-1].replace("/topics/", "/"),
                                          state=s["state"], queue=s.get("queue", 0), model=s.get("real_model") or s.get("model") or self.t("default"),
                                          cost=s.get("cost_usd", 0), turns=s.get("turns", 0), url=f"{self.web_url}/sessions/{sid}"))
        except HERR as e:
            await reply(self.err(e))
        except DiscordError as e:
            log.warning("command %s: %s", name, e)

    def allowed_here(self, ch: str, in_dm: bool) -> bool:
        """/new and /sessions list private session titles: only in a DM, a project channel or a session thread."""
        if in_dm or self.project_of(ch) or self.store.session_of_channel(ch):
            return True
        return str(ch) in {v for v in self.lay.values() if isinstance(v, str)}

    async def cmd_new(self, i: dict, ch: str, in_dm: bool, title: str, topic: str = "", text: str = "") -> None:
        sid = self.store.session_of_channel(ch)
        pid = secrets.token_hex(4)
        self.purge()
        app = self.gw.app_id
        if sid:                                                # inside a session's thread: same topic (or the one asked), new session
            link = self.store.data["threads"][sid]
            path = link["path"]
            await self.rest.respond(i, 5, ephemeral=True)
            parent = link.get("parent") or ch
            if topic:
                try:
                    path = resolve_topic(topic, [x for x in await self.topics() if x["project"] == self.project_of(parent)]) or path
                except HERR as e:
                    return await self.rest.edit_original(i, app, self.err(e))
            p = Pending(text=text, files=[], channel=parent, message="", in_dm=False, title=title)
            ok = await self.start(p, path)
            msg = self.t("started", path=path.split("projects/", 1)[-1].replace("/topics/", "/")) if ok else self.t("start_failed")
            return await self.rest.followup(i, app, msg, ephemeral=True)
        project = self.project_of(ch)
        if not in_dm and not project:
            return await self.rest.respond(i, 4, self.t("no_session"), ephemeral=True)
        await self.rest.respond(i, 5, ephemeral=True)             # the menu is private to the owner
        try:
            topics = [x for x in await self.topics() if not project or x["project"] == project]
        except HERR as e:
            return await self.rest.edit_original(i, app, self.err(e))
        path = resolve_topic(topic, topics) if topic else None
        if path:                                                  # the topic was chosen in the command: no menu
            ok = await self.start(Pending(text=text, files=[], channel=ch, message="", in_dm=in_dm, title=title, project=project), path)
            msg = self.t("started", path=path.split("projects/", 1)[-1].replace("/topics/", "/")) if ok else self.t("start_failed")
            return await self.rest.edit_original(i, app, msg)
        if topic:                                                 # typed something that is not one topic: list the matches
            low = " ".join(topic.split()).lower()
            topics = [x for x in topics if low in f"{x['project']}/{x['name']}".lower()] or topics
        self.pending[pid] = Pending(text=text, files=[], channel=ch, message="", in_dm=in_dm, title=title, project=project)
        await self.rest.edit_original(i, app, self.where(topics, len(topics) > MENU_MAX),
                                      components=self.menu(pid, topics, projects=len(topics) > MENU_MAX))

    # --- autocomplete ----------------------------------------------------------------------------------------------

    async def model_list(self) -> list[tuple[str, str]]:
        try:
            return await self.h.models() or list(FALLBACK_MODELS)
        except HERR:
            return list(FALLBACK_MODELS)

    async def on_autocomplete(self, i: dict, owner: bool) -> None:
        """Interaction type 4: up to 25 choices for the focused option (models of /model, topics of /new)."""
        choices: list[dict] = []
        focused = next((o for o in i["data"].get("options", []) if o.get("focused")), None)
        q = " ".join(str((focused or {}).get("value") or "").split()).lower()
        try:
            if owner and i["data"]["name"] == "harness":
                path, hf = focused_option(i["data"].get("options") or [])
                choices = await self.tool_choices(path, hf) if hf else []
            elif owner and focused and i["data"]["name"] == "model":
                choices = [{"name": self.t("default"), "value": "default"}] if not q or q in "default" or q in self.t("default").lower() else []
                for mid, label in await self.model_list():
                    if not q or q in mid.lower() or q in label.lower():
                        choices.append({"name": label, "value": mid})
            elif owner and focused and i["data"]["name"] == "new" and focused["name"] == "topic":
                project = self.project_of(i["channel_id"])
                if not project and self.store.session_of_channel(i["channel_id"]):
                    project = self.project_of(self.store.link_of(self.store.session_of_channel(i["channel_id"])).get("parent", ""))
                for x in await self.topics():
                    label = f"{x['project']}/{x['name']}"
                    if (project and x["project"] != project) or (q and q not in f"{label} {x['desc']}".lower()) or len(x["path"]) > 100:
                        continue
                    choices.append({"name": f"{x['emoji'] + ' ' if x['emoji'] else ''}{label}", "value": x["path"]})
        except HERR as e:
            log.warning("autocomplete %s: %s", i["data"].get("name"), e)
            choices = []
        try:
            await self.rest.autocomplete(i, choices[:25])
        except DiscordError as e:
            log.warning("autocomplete answer: %s", e)

    # --- message context menus ---------------------------------------------------------------------------------------

    async def cmd_context(self, i: dict, name: str) -> None:
        """Apps -> "Send to Claude" / "New session from message" on a message (owner only, answers are private)."""
        if name not in CTX_SEND | CTX_NEW:
            return
        ch, in_dm = i["channel_id"], not i.get("guild_id")
        app = self.gw.app_id
        target = str(i["data"].get("target_id", ""))
        m = ((i["data"].get("resolved") or {}).get("messages") or {}).get(target)
        if not m:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        text, files = self.incoming(m)
        if not text and not files:
            return await self.rest.respond(i, 4, self.t("ctx_empty"), ephemeral=True)
        sid = self.store.session_of_channel(ch)
        if not sid and not self.allowed_here(ch, in_dm):
            return await self.rest.respond(i, 4, self.t("refused_here"), ephemeral=True)
        await self.rest.respond(i, 5, ephemeral=True)
        m = {**m, "channel_id": ch}
        try:
            if sid and name in CTX_SEND:
                await self.forward(sid, m, ch)
                return await self.rest.followup(i, app, self.t("ctx_sent"), ephemeral=True)
            if sid:                                                # new session, same topic, in a fresh thread of the project channel
                link = self.store.data["threads"][sid]
                p = Pending(text=text, files=files, channel=link.get("parent") or ch, message="", in_dm=False)
                ok = await self.start(p, link["path"])
                return await self.rest.followup(i, app, self.t("started", path=link["path"].split("projects/", 1)[-1].replace("/topics/", "/"))
                                                if ok else self.t("start_failed"), ephemeral=True)
            project = self.project_of(ch)
            topics = [x for x in await self.topics() if not project or x["project"] == project]
            p = Pending(text=text, files=files, channel=ch, message=target if not in_dm else "", in_dm=in_dm, project=project)
            if len(topics) == 1:
                ok = await self.start(p, topics[0]["path"])
                return await self.rest.followup(i, app, self.t("ctx_sent") if ok else self.t("start_failed"), ephemeral=True)
            pid = secrets.token_hex(4)
            self.purge()
            self.pending[pid] = p
            await self.rest.edit_original(i, app, self.where(topics, len(topics) > MENU_MAX),
                                          components=self.menu(pid, topics, projects=len(topics) > MENU_MAX))
        except HERR as e:
            await self.rest.followup(i, app, self.err(e), ephemeral=True)
        except DiscordError as e:
            log.warning("context menu %s: %s", name, e)

    async def cmd_sessions(self, i: dict, reply) -> None:
        every = [s for s in await self.h.sessions() if not s.get("archived") and not s.get("cron")]
        items = every[:MENU_MAX]
        if not items:
            return await reply(self.t("sessions_none"))
        opts = [{"label": fmt.clip(s.get("title") or "…", 100), "value": s["id"],
                 "description": fmt.clip(s["path"].split("projects/", 1)[-1].replace("/topics/", "/"), 100)} for s in items]
        comps = [{"type": 1, "components": [{"type": 3, "custom_id": "sess:x", "placeholder": self.t("sessions_ph"), "options": opts}]}]
        note = " " + self.t("menu_cut", n=MENU_MAX) if len(every) > MENU_MAX else ""
        await self.rest.edit_original(i, self.gw.app_id, self.t("sessions_pick") + note, components=comps)     # ephemeral, like the deferral

    async def open_session(self, sid: str, channel: str, in_dm: bool, say=None) -> None:
        say = say or (lambda s: self.rest.send(channel, s))
        try:
            s = await self.h.session(sid)
        except HERR as e:
            return await say(self.err(e))
        if in_dm:
            self.store.set_dm(sid, channel)
            return await say(self.t("opened", title=s.get("title") or "…", where=self.t("hint_dm")))
        th = self.store.channel_of(sid)
        if not th:
            parent = self.lay.get("projects", {}).get(s["path"].split("/")[1] if s["path"].startswith("projects/") else "")
            if not parent:
                return await say(self.err("no channel for this project"))
            try:
                th = (await self.rest.start_thread(parent, self.thread_name(s["path"], s.get("title") or "…")))["id"]
            except DiscordError as e:
                log.warning("thread for session %s: %s", sid, e)
                return await say(self.t("start_failed"))
            self.store.link(sid, th, s["path"], parent=parent)
        await say(self.t("opened", title=s.get("title") or "…", where=f"<#{th}>"))
