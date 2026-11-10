// Harness Discord - settings page. Everything the user types is shown with textContent / values, never innerHTML.
const TXT = {
  en: {
    title: "Discord", sub: "Talk to your sessions from your Discord server.",
    connected: "Connected", offline: "Not connected", stopped: "Service stopped",
    keys: "Keys", keyMissing: "missing in Settings → Keys", keyOk: "set",
    owner: "Your Discord user id", ownerHelp: "The only person the bot answers. Discord: Settings → Advanced → Developer mode, then right-click your name → Copy user ID.",
    guild: "Server id (optional)", guildHelp: "Only needed when the bot is in several servers.",
    mode: "Replies", live: "Live status message, then the answer", final: "Only the final answer",
    feed: "Post alerts, approvals and job reports (notifications feed)", agents: "Post the results of agents (one thread each)", bell: "Bell: ring in the thread when a turn started in the web UI ends (sessions with the bell on)",
    mirror: "Mirror recent web sessions as threads (renamed and archived with the session)", reactions: "Status reactions on your messages (⏳ then ✅ / ❌)", buttons: "Stop and Regenerate buttons under the final answer", dashboard: "Pinned dashboard message in the alerts channel (needs Manage Messages)",
    save: "Save", saved: "Saved.", sync: "Resync channels", test: "Send a test message", restart: "Restart the bridge",
    done: "Asked. It happens within 10 seconds.", layout: "Channels", projects: "Project channels", system: "System channels",
    threads: "Session threads", running: "Turns running", setup: "First setup",
    errTitle: "The bridge cannot connect to Discord", errIntent: "Fix: in the Discord developer portal, Bot page, turn on \"Message Content Intent\", save, then press Restart the bridge.",
    errToken: "Fix: in the developer portal, Bot page, reset the token, paste the new one in Settings → Keys (DISCORD_BOT_TOKEN), then press Restart the bridge.",
    errBoth: "Fix: check that \"Message Content Intent\" is on (developer portal, Bot page) and that DISCORD_BOT_TOKEN is the current token (reset it if unsure), then press Restart the bridge.",
    steps: ["Create an application and a bot on the Discord developer portal, enable \"Message Content Intent\".",
            "Invite it to your server with the scopes bot + applications.commands (never Administrator; permissions: View Channels, Manage Channels, Manage Roles (to make channels private), Send Messages, Send Messages in Threads, Create Public Threads, Manage Threads, Read Message History, Add Reactions, Embed Links, Manage Messages (to pin the dashboard)).",
            "Add DISCORD_BOT_TOKEN and HARNESS_PASSWORD in Settings → Keys, and your user id below.",
            "Run install.sh from the Harness Discord repository (as root), then press Resync channels."],
  },
  fr: {
    title: "Discord", sub: "Parle à tes sessions depuis ton serveur Discord.",
    connected: "Connecté", offline: "Non connecté", stopped: "Service arrêté",
    keys: "Clés", keyMissing: "absente dans Réglages → Clés", keyOk: "définie",
    owner: "Ton identifiant Discord", ownerHelp: "La seule personne à qui le bot répond. Discord : Paramètres → Avancés → Mode développeur, puis clic droit sur ton nom → Copier l'identifiant.",
    guild: "Identifiant du serveur (facultatif)", guildHelp: "Utile seulement si le bot est dans plusieurs serveurs.",
    mode: "Réponses", live: "Message de statut en direct, puis la réponse", final: "Seulement la réponse finale",
    feed: "Publier les alertes, approbations et rapports de jobs (flux de notifications)", agents: "Publier les résultats des agents (un fil chacun)", bell: "Cloche : sonner dans le fil quand un tour lancé depuis le web se termine (sessions avec la cloche)",
    mirror: "Reproduire les sessions web récentes en fils (renommés et archivés avec la session)", reactions: "Réactions d'état sur tes messages (⏳ puis ✅ / ❌)", buttons: "Boutons Stop et Régénérer sous la réponse finale", dashboard: "Message tableau de bord épinglé dans le salon alertes (demande Gérer les messages)",
    save: "Enregistrer", saved: "Enregistré.", sync: "Resynchroniser les salons", test: "Envoyer un message de test", restart: "Redémarrer le pont",
    done: "Demandé. Ça se fait en moins de 10 secondes.", layout: "Salons", projects: "Salons de projets", system: "Salons système",
    threads: "Fils de sessions", running: "Tours en cours", setup: "Première installation",
    errTitle: "Le pont ne peut pas se connecter à Discord", errIntent: "Correction : sur le portail développeur Discord, page Bot, active « Message Content Intent », enregistre, puis appuie sur Redémarrer le pont.",
    errToken: "Correction : sur le portail développeur, page Bot, régénère le jeton, colle le nouveau dans Réglages → Clés (DISCORD_BOT_TOKEN), puis appuie sur Redémarrer le pont.",
    errBoth: "Correction : vérifie que « Message Content Intent » est activé (portail développeur, page Bot) et que DISCORD_BOT_TOKEN est le jeton actuel (régénère-le au doute), puis appuie sur Redémarrer le pont.",
    steps: ["Crée une application et un bot sur le portail développeur Discord, active « Message Content Intent ».",
            "Invite-le sur ton serveur avec les portées bot + applications.commands (jamais Administrateur ; permissions : Voir les salons, Gérer les salons, Gérer les rôles (pour rendre les salons privés), Envoyer des messages, Envoyer des messages dans les fils, Créer des fils publics, Gérer les fils, Voir les anciens messages, Ajouter des réactions, Intégrer des liens, Gérer les messages (pour épingler le tableau de bord)).",
            "Ajoute DISCORD_BOT_TOKEN et HARNESS_PASSWORD dans Réglages → Clés, et ton identifiant ci-dessous.",
            "Lance install.sh du dépôt Harness Discord (en root), puis appuie sur Resynchroniser les salons."],
  },
};

const FLAGS = ["relay_feed", "relay_agents", "relay_bell", "mirror_web_sessions", "reactions", "answer_buttons", "dashboard"];

export function mount(el, ctx) {
  const L = TXT[ctx.lang] || TXT.en;
  el.innerHTML = `
    <div class="ui-page" style="max-width:760px">
      <h1 class="ui-h1"></h1><p class="ui-muted sub"></p>
      <div class="ui-card err" role="alert" style="display:none;border:1px solid #d33;margin-bottom:12px"><div style="padding:12px"><b class="err-t"></b><div class="err-m ui-small"></div><div class="err-h"></div></div></div>
      <div class="ui-card"><div class="ui-card-header"><div class="ui-icon-box"></div><div><b class="state"></b><span class="ui-small keys"></span></div></div></div>
      <div class="ui-section ui-stack form" style="margin-top:16px">
        <div class="ui-field"><label class="ui-label owner-l"></label><input class="ui-input" name="owner_id" inputmode="numeric"><span class="ui-small ui-muted owner-h"></span></div>
        <div class="ui-field"><label class="ui-label guild-l"></label><input class="ui-input" name="guild_id" inputmode="numeric"><span class="ui-small ui-muted guild-h"></span></div>
        <div class="ui-field"><label class="ui-label mode-l"></label><select class="ui-select" name="reply_mode"></select></div>
        <label class="ui-row" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" style="flex:none;margin-top:2px" class="ui-check" name="relay_feed"><span class="feed-l"></span></label>
        <label class="ui-row" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" style="flex:none;margin-top:2px" class="ui-check" name="relay_agents"><span class="agents-l"></span></label>
        <label class="ui-row" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" style="flex:none;margin-top:2px" class="ui-check" name="relay_bell"><span class="bell-l"></span></label>
        <label class="ui-row" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" style="flex:none;margin-top:2px" class="ui-check" name="mirror_web_sessions"><span class="mirror-l"></span></label>
        <label class="ui-row" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" style="flex:none;margin-top:2px" class="ui-check" name="reactions"><span class="reactions-l"></span></label>
        <label class="ui-row" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" style="flex:none;margin-top:2px" class="ui-check" name="answer_buttons"><span class="buttons-l"></span></label>
        <label class="ui-row" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" style="flex:none;margin-top:2px" class="ui-check" name="dashboard"><span class="dashboard-l"></span></label>
        <div class="ui-row"><button class="ui-btn save"></button><span class="ui-small msg"></span></div>
      </div>
      <div class="ui-section" style="margin-top:16px"><h2 class="ui-h2 layout-t"></h2><div class="ui-stack layout"></div></div>
      <div class="ui-row" style="margin-top:16px;flex-wrap:wrap">
        <button class="ui-btn ui-secondary" data-act="sync"></button>
        <button class="ui-btn ui-secondary" data-act="test"></button>
        <button class="ui-btn ui-outline" data-act="restart"></button>
      </div>
      <div class="ui-section" style="margin-top:16px"><h2 class="ui-h2 setup-t"></h2><ol class="ui-steps"></ol></div>
    </div>`;
  const $ = (s) => el.querySelector(s);
  $("h1").textContent = L.title; $(".sub").textContent = L.sub;
  $(".owner-l").textContent = L.owner; $(".owner-h").textContent = L.ownerHelp;
  $(".guild-l").textContent = L.guild; $(".guild-h").textContent = L.guildHelp;
  $(".mode-l").textContent = L.mode; $(".feed-l").textContent = L.feed; $(".agents-l").textContent = L.agents; $(".bell-l").textContent = L.bell;
  $(".mirror-l").textContent = L.mirror; $(".reactions-l").textContent = L.reactions; $(".buttons-l").textContent = L.buttons; $(".dashboard-l").textContent = L.dashboard;
  $(".save").textContent = L.save; $(".layout-t").textContent = L.layout; $(".setup-t").textContent = L.setup;
  $(".ui-icon-box").append(ctx.icon("message-square"));
  for (const b of el.querySelectorAll("[data-act]")) b.textContent = L[b.dataset.act];
  const sel = $("select");
  for (const v of ["live", "final"]) { const o = document.createElement("option"); o.value = v; o.textContent = L[v]; sel.append(o); }
  for (const s of L.steps) { const li = document.createElement("li"); li.textContent = s; $(".ui-steps").append(li); }

  let mt; const msg = (t) => { $(".msg").textContent = t; clearTimeout(mt); mt = setTimeout(() => ($(".msg").textContent = ""), 4000); };
  const row = (k, v) => { const d = document.createElement("div"); d.className = "ui-row"; const a = document.createElement("span"); a.className = "ui-muted"; a.textContent = k; const b = document.createElement("span"); b.textContent = v; d.append(a, b); return d; };
  let loaded = false;
  const render = (s) => {
    const ok = s.connected;
    $(".state").textContent = s.service !== "active" ? L.stopped : ok ? L.connected : L.offline;
    const err = $(".err"), text = s.error || "";
    err.style.display = text ? "" : "none";
    if (text) {
      $(".err-t").textContent = L.errTitle; $(".err-m").textContent = text;
      $(".err-h").textContent = /4014|4013|intent/i.test(text) ? L.errIntent : /4004|token/i.test(text) ? L.errToken : L.errBoth;
    }
    $(".keys").textContent = ` · DISCORD_BOT_TOKEN: ${s.keys.token ? L.keyOk : L.keyMissing} · HARNESS_PASSWORD: ${s.keys.password ? L.keyOk : L.keyMissing}`;
    if (!loaded) {
      const f = $(".form");
      f.querySelector("[name=owner_id]").value = s.config.owner_id;
      f.querySelector("[name=guild_id]").value = s.config.guild_id;
      f.querySelector("[name=reply_mode]").value = s.config.reply_mode;
      f.querySelector("[name=relay_feed]").checked = s.config.relay_feed;
      f.querySelector("[name=relay_agents]").checked = s.config.relay_agents;
      for (const k of FLAGS) f.querySelector(`[name=${k}]`).checked = s.config[k];
      loaded = true;
    }
    $(".layout").replaceChildren(
      row(L.system, s.layout.system.join(", ") || "—"), row(L.projects, s.layout.projects.join(", ") || "—"),
      row(L.threads, String(s.threads)), row(L.running, String(s.running)));
  };
  const load = () => ctx.api("status").then(render).catch(() => {});
  load();
  const timer = setInterval(load, 10000);

  const save = async () => {
    const f = $(".form"), q = (n) => f.querySelector(`[name=${n}]`);
    try {
      render(await ctx.api("config", { method: "POST", body: { owner_id: q("owner_id").value, guild_id: q("guild_id").value, reply_mode: q("reply_mode").value, relay_feed: q("relay_feed").checked, relay_agents: q("relay_agents").checked, relay_bell: q("relay_bell").checked, ...Object.fromEntries(FLAGS.slice(3).map((k) => [k, q(k).checked])) } }));
      msg(L.saved);
    } catch (e) { msg(String(e.message || e)); }
  };
  const act = async (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    if (b.dataset.act === "restart" && !confirm(L.restart + " ?")) return;
    try { await ctx.api("action", { method: "POST", body: { action: b.dataset.act } }); msg(L.done); } catch (err) { msg(String(err.message || err)); }
  };
  $(".save").addEventListener("click", save);
  el.addEventListener("click", act);
  return () => { clearInterval(timer); el.removeEventListener("click", act); };
}
