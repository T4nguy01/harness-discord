# Discord embeds (as the bridge uses them)

The bridge talks to the REST API with plain dicts (no discord.js): an embed is a JSON object in `embeds: [...]`
(`rest.send(ch, "", embeds=[e])`, `rest.edit(ch, msg, embeds=[e])`). Reference: https://discordjs.guide/legacy/popular-topics/embeds
(discord.js `EmbedBuilder` = the same fields with setters; snake_case in raw JSON: `icon_url`).

## Fields (all optional)
| Field | Form | Limit |
|---|---|---|
| `title` | string (+ `url` makes it a link) | 256 |
| `description` | string, markdown | 4096 |
| `color` | integer, e.g. `0x5865F2` (not a "#hex" string in raw JSON) | |
| `author` | `{name, url, icon_url}` | name 256 |
| `fields` | `[{name, value, inline}]`, 3 inline per row | 25 fields, name 256, value 1024 |
| `thumbnail` / `image` | `{url}` | |
| `footer` | `{text, icon_url}` | 2048 |
| `timestamp` | ISO 8601 string (UTC) | |
- Total of all embeds of a message: **6000 characters**; at most 10 embeds per message. `fmt.fit_embed(e)` trims an embed to
  these limits (use it before sending anything built from user or agent text); `fmt.embed_size(e)` counts.
- Blank field: `{"name": "​", "value": "​"}`. An empty `name` or `value` makes Discord reject the message.
- Markdown works in `description` and field values, not in `title` / `footer` / field names. Links `[text](url)` only in
  description and field values. Mentions in embeds do not ping.
- Embeds carry no buttons: components go next to them (`components=[...]`, see `relay.render`).

## Images
- External URL: `{"image": {"url": "https://..."}}`.
- Uploaded file: send the file as a multipart attachment and reference it as `attachment://name.png`. If the image shows
  outside the embed instead of inside, the file name in the URL does not match the upload.
- Only the CDN of Discord is trusted for incoming attachments; do not fetch other hosts for the user.

## Conventions of the bridge
- Colour = level: `relay.COLOR` info `0x5865F2`, success `0x57F287`, warning `0xFEE75C`, error `0xED4245`; resolved / done `0x95A5A6`.
- Feed event: title `<level emoji> title` (<= 240), body in `description` (<= 3500), resolution in a `✓` field, footer = source,
  `timestamp` = event time. Agent result: `author` = `✅|🛑 agent`, body in `description`, split in <= 5 parts, footer = cost / duration.
- Edit rather than repost (`rest.edit`): a resolved event keeps its message and turns grey.
- Never put keys, tokens or the Harness password in an embed. Do not send private memory content to a public channel.

## Checklist for a new embed
1. Pick the colour from the level, a short title, the body in `description`, details in at most a few inline fields.
2. Pass it through `fmt.fit_embed`.
3. Send with `silent=True` for low-importance posts.
4. Test offline (`tests/`) on the dict: sizes, required keys; say that the Discord rendering itself is not verified unless seen.
