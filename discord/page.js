// Harness Discord - settings page. Everything the user types or the bridge reports is shown with textContent / values,
// never innerHTML (the elements are built with h()).
const TXT = {
  en: {
    title: "Discord", sub: "Talk to your Harness sessions from your Discord server.",
    connected: "Connected", offline: "Not connected", stopped: "Service stopped", starting: "Starting…",
    beat: "last signal", version: "Version", bot: "Bot", server: "Server", threads: "Session threads", running: "Turns running", projects: "Project channels",
    errTitle: "The bridge cannot connect to Discord",
    errIntent: "Fix: in the Discord developer portal, Bot page, turn on \"Message Content Intent\", save, then press Restart the bridge.",
    errToken: "Fix: in the developer portal, Bot page, reset the token, paste the new one in Settings → Keys (DISCORD_BOT_TOKEN), then press Restart the bridge.",
    errBoth: "Fix: check that \"Message Content Intent\" is on (developer portal, Bot page) and that DISCORD_BOT_TOKEN is the current token (reset it if unsure), then press Restart the bridge.",
    checkT: "Setup checklist", allOk: "Everything is in place.",
    cToken: "DISCORD_BOT_TOKEN key set (Settings → Keys)", cPass: "HARNESS_PASSWORD key set (Settings → Keys, 2FA must stay off)", cOwner: "Your Discord user id saved",
    cService: "Bridge service running", cConn: "Connected to Discord", cGuild: "Bot is in your server and channels are created",
    accessT: "Access", ownerL: "Your Discord user id", ownerH: "The only person the bot answers. Discord: Settings → Advanced → Developer mode, then right-click your name → Copy user ID.",
    guildL: "Server id (optional)", guildH: "Only needed when the bot is in several servers. Right-click the server icon → Copy server ID.",
    idBad: "A Discord id is 15 to 25 digits.",
    repliesT: "Replies", modeL: "How answers appear", live: "Live status message, then the answer", final: "Only the final answer",
    relayT: "What the bot posts by itself",
    feed: ["Notifications feed", "Alerts, approvals and job reports, in the system channels."],
    agents: ["Agent results", "One thread per agent, with its results."],
    bell: ["Bell", "Rings in the thread when a turn started in the web UI ends (sessions with the bell on)."],
    proactive: ["Proactive messages", "Morning task summary (08:00), plan above 80 %, failed backup."],
    dashboard: ["Pinned dashboard", "A live message pinned in the alerts channel. Needs Manage Messages."],
    comfortT: "Comfort in the threads",
    mirror: ["Mirror web sessions", "A thread for every recent web session, renamed and archived with it."],
    reactions: ["Status reactions", "⏳ while Claude works, then ✅ or ❌ on your message."],
    answer_buttons: ["Answer buttons", "Stop and Regenerate under the final answer."],
    safetyT: "Safety",
    tool_approvals: ["Tool-call approvals", "Allow / Deny / Always buttons in the thread. Same queue as the web UI and Telegram: the first answer wins."],
    save: "Save", saved: "Saved.", dirty: "Unsaved changes", revert: "Revert", clean: "Up to date", applies: "Applies within 10 seconds (agents: 30 s, server id: next resync).",
    actionsT: "Actions", sync: "Resync channels", test: "Send a test message", restart: "Restart the bridge", done: "Asked. It happens within 10 seconds.", restarted: "Restart requested.",
    layoutT: "Channels created", sysCh: "System", projCh: "Projects", none: "none yet",
    inviteT: "Invite the bot", inviteH: "Opens Discord to add (or re-invite) the bot with exactly the permissions it needs. Never Administrator.",
    inviteBtn: "Open the invite link", copy: "Copy", copied: "Copied.", noBot: "Appears once the bridge has connected once.", perms: "Permission integer",
    auditT: "Recent actions from Discord", auditNone: "No action yet.", when: "When", cmd: "Command", target: "Target", result: "Result",
    guideT: "Setup guide (step by step)", permList: "Permissions requested: View Channels, Manage Channels, Manage Roles (private channels), Send Messages, Send Messages in Threads, Create Public Threads, Manage Threads, Read Message History, Add Reactions, Embed Links, Manage Messages (pin the dashboard). Never Administrator.",
    steps: ["Create an application and a bot on the Discord developer portal; on the Bot page enable \"Message Content Intent\".",
            "Invite the bot with the link below (scopes bot + applications.commands).",
            "Add DISCORD_BOT_TOKEN and HARNESS_PASSWORD in Settings → Keys, and your user id above.",
            "Run install.sh from the Harness Discord repository (as root), then press Resync channels."],
  },
  fr: {
    title: "Discord", sub: "Parle à tes sessions Harness depuis ton serveur Discord.",
    connected: "Connecté", offline: "Non connecté", stopped: "Service arrêté", starting: "Démarrage…",
    beat: "dernier signal", version: "Version", bot: "Bot", server: "Serveur", threads: "Fils de sessions", running: "Tours en cours", projects: "Salons de projets",
    errTitle: "Le pont ne peut pas se connecter à Discord",
    errIntent: "Correction : sur le portail développeur Discord, page Bot, active « Message Content Intent », enregistre, puis appuie sur Redémarrer le pont.",
    errToken: "Correction : sur le portail développeur, page Bot, régénère le jeton, colle le nouveau dans Réglages → Clés (DISCORD_BOT_TOKEN), puis appuie sur Redémarrer le pont.",
    errBoth: "Correction : vérifie que « Message Content Intent » est activé (portail développeur, page Bot) et que DISCORD_BOT_TOKEN est le jeton actuel (régénère-le au doute), puis appuie sur Redémarrer le pont.",
    checkT: "Liste de contrôle", allOk: "Tout est en place.",
    cToken: "Clé DISCORD_BOT_TOKEN définie (Réglages → Clés)", cPass: "Clé HARNESS_PASSWORD définie (Réglages → Clés, la 2FA doit rester désactivée)", cOwner: "Ton identifiant Discord enregistré",
    cService: "Service du pont en marche", cConn: "Connecté à Discord", cGuild: "Le bot est sur ton serveur et les salons sont créés",
    accessT: "Accès", ownerL: "Ton identifiant Discord", ownerH: "La seule personne à qui le bot répond. Discord : Paramètres → Avancés → Mode développeur, puis clic droit sur ton nom → Copier l'identifiant.",
    guildL: "Identifiant du serveur (facultatif)", guildH: "Utile seulement si le bot est dans plusieurs serveurs. Clic droit sur l'icône du serveur → Copier l'identifiant.",
    idBad: "Un identifiant Discord fait 15 à 25 chiffres.",
    repliesT: "Réponses", modeL: "Affichage des réponses", live: "Message de statut en direct, puis la réponse", final: "Seulement la réponse finale",
    relayT: "Ce que le bot publie seul",
    feed: ["Flux de notifications", "Alertes, approbations et rapports de jobs, dans les salons système."],
    agents: ["Résultats des agents", "Un fil par agent, avec ses résultats."],
    bell: ["Cloche", "Sonne dans le fil quand un tour lancé depuis le web se termine (sessions avec la cloche)."],
    proactive: ["Messages proactifs", "Résumé des tâches du matin (8 h), forfait au-dessus de 80 %, sauvegarde en échec."],
    dashboard: ["Tableau de bord épinglé", "Un message en direct épinglé dans le salon alertes. Demande Gérer les messages."],
    comfortT: "Confort dans les fils",
    mirror: ["Reproduire les sessions web", "Un fil par session web récente, renommé et archivé avec elle."],
    reactions: ["Réactions d'état", "⏳ pendant que Claude travaille, puis ✅ ou ❌ sur ton message."],
    answer_buttons: ["Boutons de réponse", "Stop et Régénérer sous la réponse finale."],
    safetyT: "Sécurité",
    tool_approvals: ["Approbation des appels d'outils", "Boutons Autoriser / Refuser / Toujours dans le fil. Même file que l'interface web et Telegram : la première réponse gagne."],
    save: "Enregistrer", saved: "Enregistré.", dirty: "Modifications non enregistrées", revert: "Annuler", clean: "À jour", applies: "Appliqué en moins de 10 secondes (agents : 30 s, serveur : prochaine synchro).",
    actionsT: "Actions", sync: "Resynchroniser les salons", test: "Envoyer un message de test", restart: "Redémarrer le pont", done: "Demandé. Ça se fait en moins de 10 secondes.", restarted: "Redémarrage demandé.",
    layoutT: "Salons créés", sysCh: "Système", projCh: "Projets", none: "aucun pour l'instant",
    inviteT: "Inviter le bot", inviteH: "Ouvre Discord pour ajouter (ou ré-inviter) le bot avec exactement les permissions utiles. Jamais Administrateur.",
    inviteBtn: "Ouvrir le lien d'invitation", copy: "Copier", copied: "Copié.", noBot: "Apparaît dès que le pont s'est connecté une fois.", perms: "Entier de permissions",
    auditT: "Dernières actions depuis Discord", auditNone: "Aucune action pour l'instant.", when: "Quand", cmd: "Commande", target: "Cible", result: "Résultat",
    guideT: "Guide d'installation (pas à pas)", permList: "Permissions demandées : Voir les salons, Gérer les salons, Gérer les rôles (salons privés), Envoyer des messages, Envoyer des messages dans les fils, Créer des fils publics, Gérer les fils, Voir les anciens messages, Ajouter des réactions, Intégrer des liens, Gérer les messages (épingler le tableau de bord). Jamais Administrateur.",
    steps: ["Crée une application et un bot sur le portail développeur Discord ; page Bot, active « Message Content Intent ».",
            "Invite le bot avec le lien ci-dessous (portées bot + applications.commands).",
            "Ajoute DISCORD_BOT_TOKEN et HARNESS_PASSWORD dans Réglages → Clés, et ton identifiant ci-dessus.",
            "Lance install.sh du dépôt Harness Discord (en root), puis appuie sur Resynchroniser les salons."],
  },
};

const GROUPS = [
  ["relayT", ["relay_feed", "relay_agents", "relay_bell", "proactive", "dashboard"], ["feed", "agents", "bell", "proactive", "dashboard"]],
  ["comfortT", ["mirror_web_sessions", "reactions", "answer_buttons"], ["mirror", "reactions", "answer_buttons"]],
  ["safetyT", ["tool_approvals"], ["tool_approvals"]],
];
const FLAGS = GROUPS.flatMap((g) => g[1]);
const ID_RE = /^\d{15,25}$/;

function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v; else if (k === "style") e.style.cssText = v; else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  e.append(...kids.flat().filter((x) => x != null));
  return e;
}

export function mount(el, ctx) {
  const L = TXT[ctx.lang] || TXT.en;
  let status = null, baseline = null, msgTimer;
  const inputs = {}, toggles = {};
  const ago = (s) => (s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : s < 129600 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`);

  // --- static skeleton -----------------------------------------------------------------------------------------------
  const dot = h("span", { class: "ui-dot", style: "flex:none" });
  const stateT = h("b", { style: "font-size:1.05rem" });
  const stateS = h("span", { class: "ui-small ui-muted" });
  const errBox = h("div", { class: "ui-callout ui-error", role: "alert", style: "display:none" });
  const tiles = h("div", { class: "ui-grid", style: "display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px" });
  const checkList = h("div", { class: "ui-stack", style: "gap:6px" });
  const checkBar = h("div", { class: "ui-progress", style: "height:6px;margin:6px 0 10px" }, h("div", { style: "height:100%;border-radius:inherit;background:#5865f2;transition:width .3s;width:0" }));
  const checkT = h("b", {});
  const msg = h("span", { class: "ui-small", "aria-live": "polite" });
  const dirtyT = h("span", { class: "ui-small ui-muted" });
  const saveBtn = h("button", { class: "ui-btn ui-primary", text: L.save, onclick: () => save() });
  const revertBtn = h("button", { class: "ui-btn ui-ghost", text: L.revert, onclick: () => { fill(baseline); refreshDirty(); } });
  const layoutBox = h("div", { class: "ui-stack", style: "gap:8px" });
  const auditBox = h("div", {});
  const inviteBox = h("div", { class: "ui-stack", style: "gap:8px" });

  const card = (title, ...kids) => h("div", { class: "ui-card" }, h("div", { style: "padding:14px 16px" }, title ? h("h2", { class: "ui-h3", style: "margin:0 0 10px", text: title }) : null, ...kids));

  const idField = (name, label, help) => {
    const inp = h("input", { class: "ui-input", name, inputmode: "numeric", autocomplete: "off", spellcheck: "false", placeholder: "123456789012345678" });
    const bad = h("span", { class: "ui-small", style: "color:var(--danger,#d33);display:none", text: L.idBad });
    inp.addEventListener("input", () => { bad.style.display = inp.value.trim() && !ID_RE.test(inp.value.trim()) ? "" : "none"; refreshDirty(); });
    inputs[name] = inp;
    return h("div", { class: "ui-field" }, h("label", { class: "ui-label", text: label }), inp, bad, h("span", { class: "ui-small ui-muted", text: help }));
  };
  const modeSel = h("select", { class: "ui-select", name: "reply_mode" },
    ...["live", "final"].map((v) => h("option", { value: v, text: L[v] })));
  modeSel.addEventListener("change", refreshDirty);
  inputs.reply_mode = modeSel;

  const toggle = (name, [title, desc]) => {
    const cb = h("input", { type: "checkbox", class: "ui-check", name, style: "flex:none;margin-top:3px" });
    cb.addEventListener("change", refreshDirty);
    toggles[name] = cb;
    return h("label", { class: "ui-row", style: "flex-wrap:nowrap;align-items:flex-start;gap:10px;cursor:pointer" }, cb,
      h("span", {}, h("b", { text: title }), h("br"), h("span", { class: "ui-small ui-muted", text: desc })));
  };

  const guide = h("details", {}, h("summary", { class: "ui-small", style: "cursor:pointer", text: L.guideT }),
    h("ol", { class: "ui-steps", style: "margin-top:8px" }, ...L.steps.map((s) => h("li", { text: s }))));

  el.replaceChildren(h("div", { class: "ui-page", style: "max-width:820px;display:flex;flex-direction:column;gap:14px" },
    h("div", {}, h("h1", { class: "ui-h1", style: "margin:0", text: L.title }), h("p", { class: "ui-muted", style: "margin:4px 0 0", text: L.sub })),
    errBox,
    card(null, h("div", { class: "ui-row", style: "gap:10px;flex-wrap:nowrap;align-items:center" }, dot, h("div", {}, stateT, h("br"), stateS)), h("div", { style: "height:12px" }), tiles),
    card(null, checkT, checkBar, checkList, guide),
    card(L.accessT, h("div", { class: "ui-stack", style: "gap:14px" }, idField("owner_id", L.ownerL, L.ownerH), idField("guild_id", L.guildL, L.guildH))),
    card(L.repliesT, h("div", { class: "ui-field" }, h("label", { class: "ui-label", text: L.modeL }), modeSel)),
    ...GROUPS.map(([t, names, keys]) => card(L[t], h("div", { class: "ui-stack", style: "gap:12px" }, ...names.map((n, i) => toggle(n, L[keys[i]]))))),
    h("div", { class: "ui-card", style: "position:sticky;bottom:8px;z-index:5" },
      h("div", { class: "ui-row", style: "padding:10px 16px;gap:10px;flex-wrap:wrap;align-items:center" }, saveBtn, revertBtn, dirtyT, msg, h("span", { class: "ui-small ui-muted", style: "margin-left:auto", text: L.applies }))),
    card(L.actionsT, h("div", { class: "ui-row", style: "gap:8px;flex-wrap:wrap" },
      h("button", { class: "ui-btn ui-secondary", text: L.sync, onclick: () => act("sync") }),
      h("button", { class: "ui-btn ui-secondary", text: L.test, onclick: () => act("test") }),
      h("button", { class: "ui-btn ui-outline", text: L.restart, onclick: () => { if (confirm(L.restart + " ?")) act("restart"); } }))),
    card(L.layoutT, layoutBox),
    card(L.inviteT, inviteBox),
    card(L.auditT, auditBox)));
  dot.parentElement.prepend(ctx.icon ? ctx.icon("message-square") : "");

  // --- helpers -------------------------------------------------------------------------------------------------------
  const say = (t, bad) => { msg.textContent = t; msg.style.color = bad ? "var(--danger,#d33)" : ""; clearTimeout(msgTimer); msgTimer = setTimeout(() => (msg.textContent = ""), 5000); };
  const current = () => ({ owner_id: inputs.owner_id.value.trim(), guild_id: inputs.guild_id.value.trim(), reply_mode: modeSel.value, ...Object.fromEntries(FLAGS.map((k) => [k, toggles[k].checked])) });
  const valid = (c) => (!c.owner_id || ID_RE.test(c.owner_id)) && (!c.guild_id || ID_RE.test(c.guild_id));
  function refreshDirty() {
    const c = current(), d = baseline && JSON.stringify(c) !== JSON.stringify(baseline);
    saveBtn.disabled = !d || !valid(c); revertBtn.style.display = d ? "" : "none";
    dirtyT.textContent = d ? L.dirty : L.clean;
  }
  function fill(c) {
    inputs.owner_id.value = c.owner_id; inputs.guild_id.value = c.guild_id; modeSel.value = c.reply_mode;
    for (const k of FLAGS) toggles[k].checked = !!c[k];
    for (const n of ["owner_id", "guild_id"]) inputs[n].dispatchEvent(new Event("input"));
  }
  const copyBtn = (text) => h("button", { class: "ui-btn ui-sm ui-ghost", text: L.copy, onclick: async (e) => {
    try { await navigator.clipboard.writeText(text); e.target.textContent = L.copied; setTimeout(() => (e.target.textContent = L.copy), 1500); } catch { /* clipboard blocked */ }
  } });
  const kv = (k, v) => h("div", { class: "ui-row", style: "justify-content:space-between;gap:10px;flex-wrap:wrap" }, h("span", { class: "ui-muted", text: k }), h("span", { style: "text-align:right;word-break:break-word", text: v }));
  const tile = (label, value) => h("div", { class: "ui-tile ui-stat", style: "padding:8px 10px;border:1px solid var(--border,rgba(128,128,128,.25));border-radius:8px" }, h("div", { class: "ui-stat-label ui-small ui-muted", text: label }), h("div", { class: "ui-stat-value", style: "font-weight:600;font-size:1.15rem;word-break:break-all", text: String(value) }));
  const chip = (t) => h("span", { class: "ui-badge ui-neutral", text: t });

  // --- render --------------------------------------------------------------------------------------------------------
  function render(s) {
    status = s;
    const svc = s.service === "active", conn = s.connected;
    const color = !svc ? "#d33" : conn ? "#2a9d4b" : "#e0a100";
    dot.style.cssText = `flex:none;display:inline-block;width:10px;height:10px;border-radius:50%;background:${color}`;
    stateT.textContent = !svc ? L.stopped : conn ? L.connected : s.alive ? L.offline : L.starting;
    stateS.textContent = s.age != null ? `${L.beat}: ${ago(s.age)}` : "";

    const text = s.error || "";
    errBox.style.display = text ? "" : "none";
    if (text) {
      errBox.replaceChildren(h("b", { text: L.errTitle }), h("div", { class: "ui-small", text }),
        h("div", { text: /4014|4013|intent/i.test(text) ? L.errIntent : /4004|token|401/i.test(text) ? L.errToken : L.errBoth }));
    }

    tiles.replaceChildren(tile(L.version, s.version || "—"), tile(L.threads, s.threads), tile(L.running, s.running), tile(L.projects, s.layout.projects.length));

    const checks = [
      [s.keys.token, L.cToken], [s.keys.password, L.cPass], [ID_RE.test(s.config.owner_id), L.cOwner],
      [svc, L.cService], [conn, L.cConn], [!!(s.layout.guild && s.layout.system.length), L.cGuild]];
    const okN = checks.filter((c) => c[0]).length;
    checkT.textContent = okN === checks.length ? `✅ ${L.allOk}` : `${L.checkT} · ${okN}/${checks.length}`;
    checkBar.firstChild.style.width = `${Math.round((okN / checks.length) * 100)}%`;
    checkList.replaceChildren(...checks.map(([ok, t]) => h("div", { class: "ui-row", style: "gap:8px;flex-wrap:nowrap" },
      h("span", { style: `flex:none;width:1.2em;color:${ok ? "#2a9d4b" : "#d33"}`, text: ok ? "✓" : "✗" }), h("span", { class: ok ? "ui-muted" : "", text: t }))));
    guide.open = okN < 3 ? true : guide.open;

    if (!baseline) { baseline = { owner_id: s.config.owner_id, guild_id: s.config.guild_id, reply_mode: s.config.reply_mode, ...Object.fromEntries(FLAGS.map((k) => [k, !!s.config[k]])) }; fill(baseline); refreshDirty(); }

    layoutBox.replaceChildren(
      kv(L.server, s.layout.guild || "—"),
      kv(L.sysCh, ""), h("div", { class: "ui-row", style: "gap:6px;flex-wrap:wrap" }, ...(s.layout.system.length ? s.layout.system.map((n) => chip("#" + n)) : [h("span", { class: "ui-muted ui-small", text: L.none })])),
      kv(L.projCh, ""), h("div", { class: "ui-row", style: "gap:6px;flex-wrap:wrap" }, ...(s.layout.projects.length ? s.layout.projects.map((n) => chip("#" + n)) : [h("span", { class: "ui-muted ui-small", text: L.none })])));

    inviteBox.replaceChildren(h("p", { class: "ui-small ui-muted", style: "margin:0", text: L.inviteH }), h("p", { class: "ui-small ui-muted", style: "margin:0", text: L.permList }));
    if (s.invite) {
      inviteBox.append(h("div", { class: "ui-row", style: "gap:8px;flex-wrap:wrap" },
        h("a", { class: "ui-btn ui-secondary", href: s.invite, target: "_blank", rel: "noopener noreferrer", text: L.inviteBtn }), copyBtn(s.invite)),
        h("div", { class: "ui-row ui-small ui-muted", style: "gap:8px" }, h("span", { text: `${L.perms}: ` }), h("code", { class: "ui-code", text: String(s.permissions) }), copyBtn(String(s.permissions))));
    } else inviteBox.append(h("span", { class: "ui-small ui-muted", text: L.noBot }));

    if (!s.audit.length) auditBox.replaceChildren(h("span", { class: "ui-small ui-muted", text: L.auditNone }));
    else auditBox.replaceChildren(h("div", { class: "ui-table-wrap", style: "overflow-x:auto" }, h("table", { class: "ui-table", style: "width:100%" },
      h("thead", {}, h("tr", {}, ...[L.when, L.cmd, L.target, L.result].map((t) => h("th", { style: "text-align:left", text: t })))),
      h("tbody", {}, ...s.audit.map((a) => h("tr", {}, h("td", { text: a.at ? new Date(a.at * 1000).toLocaleString(ctx.lang === "fr" ? "fr-FR" : undefined) : "" }), h("td", {}, h("code", { class: "ui-code", text: a.cmd })), h("td", { text: a.target }), h("td", { text: a.result })))))));
  }

  const load = () => ctx.api("status").then(render).catch(() => {});
  load();
  const timer = setInterval(load, 10000);

  async function save() {
    const c = current();
    if (!valid(c)) return;
    saveBtn.disabled = true;
    try {
      const s = await ctx.api("config", { method: "POST", body: c });
      baseline = c; render(s); refreshDirty(); say(L.saved);
    } catch (e) { say(String(e.message || e), true); refreshDirty(); }
  }
  async function act(a) {
    try {
      const r = await ctx.api("action", { method: "POST", body: { action: a } });
      if (r && r.ok === false) say(r.error || "error", true); else say(a === "restart" ? L.restarted : L.done);
      if (a === "restart") setTimeout(load, 4000);
    } catch (err) { say(String(err.message || err), true); }
  }
  return () => { clearInterval(timer); clearTimeout(msgTimer); };
}
