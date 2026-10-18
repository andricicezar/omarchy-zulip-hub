.pragma library

function emptyState(errorText) {
  return {
    schema_version: 1,
    connected: false,
    last_sync: null,
    unread: { total: 0, mentions: 0, private: 0 },
    recent: [],
    accounts: [],
    max_message_length: 10000,
    error: errorText || ""
  }
}

function normalizeAccounts(rows) {
  if (!Array.isArray(rows)) return []
  var result = []
  for (var index = 0; index < rows.length; index++) {
    var row = rows[index]
    if (!row || typeof row !== "object" || !row.site) continue
    var unread = row.unread && typeof row.unread === "object" ? row.unread : {}
    result.push({
      site: String(row.site),
      name: String(row.name || row.site),
      connected: row.connected === true,
      error: row.error ? String(row.error) : "",
      last_sync: row.last_sync || null,
      unread: {
        total: nonNegativeInt(unread.total),
        mentions: nonNegativeInt(unread.mentions),
        private: nonNegativeInt(unread.private)
      }
    })
  }
  return result
}

function nonNegativeInt(value) {
  var number = parseInt(String(value === undefined ? 0 : value), 10)
  return isFinite(number) && number > 0 ? number : 0
}

function normalizeRecent(rows) {
  if (!Array.isArray(rows)) return []
  var result = []
  for (var index = 0; index < rows.length; index++) {
    var row = rows[index]
    if (!row || typeof row !== "object") continue
    var id = parseInt(String(row.id), 10)
    if (!isFinite(id)) continue
    result.push({
      id: id,
      type: row.type === "private" ? "private" : "stream",
      sender: String(row.sender || "Unknown sender"),
      sender_id: nonNegativeInt(row.sender_id),
      channel: row.channel === null || row.channel === undefined ? "" : String(row.channel),
      topic: row.topic === null || row.topic === undefined ? "" : String(row.topic),
      timestamp: nonNegativeInt(row.timestamp),
      site: row.site ? String(row.site) : "",
      account: row.account ? String(row.account) : "",
      flags: Array.isArray(row.flags) ? row.flags : [],
      recipient_ids: Array.isArray(row.recipient_ids)
        ? row.recipient_ids.map(nonNegativeInt).filter(function(value) { return value > 0 })
        : []
    })
  }
  return result
}

function parseState(raw) {
  var text = String(raw || "").trim()
  if (!text) return { ok: false, state: emptyState("Waiting for Zulip Hub") }
  try {
    var parsed = JSON.parse(text)
    if (!parsed || parsed.schema_version !== 1 || typeof parsed.unread !== "object")
      return { ok: false, state: emptyState("Unsupported state format") }
    return {
      ok: true,
      state: {
        schema_version: 1,
        connected: parsed.connected === true,
        last_sync: parsed.last_sync || null,
        unread: {
          total: nonNegativeInt(parsed.unread.total),
          mentions: nonNegativeInt(parsed.unread.mentions),
          private: nonNegativeInt(parsed.unread.private)
        },
        recent: normalizeRecent(parsed.recent),
        accounts: normalizeAccounts(parsed.accounts),
        max_message_length: nonNegativeInt(parsed.max_message_length) || 10000,
        server_url: parsed.server_url ? String(parsed.server_url) : "",
        error: parsed.error ? String(parsed.error) : ""
      }
    }
  } catch (error) {
    return { ok: false, state: emptyState("Invalid Zulip Hub state") }
  }
}

function barLabel(state, showTotal, vertical) {
  var count = state && state.unread ? nonNegativeInt(state.unread.total) : 0
  var suffix = showTotal && count > 0 ? (count > 99 ? "99+" : String(count)) : ""
  if (vertical) return suffix ? "Z\n" + suffix : "Z"
  return suffix ? "Z  " + suffix : "Z"
}

function statusText(state, loaded) {
  if (!loaded) return "Waiting for bridge state"
  if (state.connected) return "Connected"
  if (state.error) return state.error
  return "Bridge offline"
}

function conversation(row) {
  if (!row) return ""
  if (row.type === "private") return "Direct message"
  if (row.channel && row.topic) return "#" + row.channel + "  ›  " + row.topic
  if (row.channel) return "#" + row.channel
  return "Channel message"
}

function relativeTime(timestamp, nowMilliseconds) {
  var seconds = nonNegativeInt(timestamp)
  if (seconds === 0) return ""
  var delta = Math.max(0, Math.floor(nowMilliseconds / 1000) - seconds)
  if (delta < 60) return "now"
  if (delta < 3600) return Math.floor(delta / 60) + "m"
  if (delta < 86400) return Math.floor(delta / 3600) + "h"
  return Math.floor(delta / 86400) + "d"
}

function isMention(row) {
  if (!row || !Array.isArray(row.flags)) return false
  var mentionFlags = [
    "mentioned", "wildcard_mentioned", "stream_wildcard_mentioned", "topic_wildcard_mentioned"
  ]
  for (var index = 0; index < mentionFlags.length; index++)
    if (row.flags.indexOf(mentionFlags[index]) !== -1) return true
  return false
}
