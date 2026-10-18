import QtQuick
import Quickshell.Io
import "I18n.js" as I18n

// Affiche un message dans le panneau. Le corps n existe pas en local : il est
// demande au pont a l ouverture et garde en memoire seulement, puis efface a la
// fermeture. Rien n est ecrit sur disque.
QtObject {
  id: root

  readonly property bool available: true
  property bool open: false
  property bool busy: process.running
  property var message: null
  property string error: ""
  property var pendingRequest: null
  property string responseLine: ""

  readonly property string localeName: Qt.locale().name
  readonly property string runnerPath: decodeURIComponent(
    String(Qt.resolvedUrl("../run-plugin.py")).replace(/^file:\/\//, ""))

  readonly property string destination: {
    if (!root.message) return ""
    if (root.message.type === "stream") {
      var channel = String(root.message.channel || "")
      var topic = String(root.message.topic || "")
      return (channel ? "#" + channel : root.t("channelMessage"))
        + (topic ? "  \u203a  " + topic : "")
    }
    return root.t("directConversation")
  }

  function t(key) { return I18n.text(key, root.localeName) }

  function show(row) {
    if (!row || process.running) return false
    var identifier = Number(row.id) || 0
    if (identifier <= 0) return false
    root.error = ""
    root.message = null
    root.responseLine = ""
    root.open = true
    root.pendingRequest = { action: "read", message_id: identifier, site: String(row.site || "") }
    process.stdinEnabled = true
    process.command = ["/usr/bin/python3", root.runnerPath, "read-message"]
    process.running = true
    return true
  }

  function close() {
    root.open = false
    root.message = null
    root.error = ""
  }

  function applyResponse(raw) {
    var response
    try {
      response = JSON.parse(String(raw || ""))
    } catch (exception) {
      root.error = root.t("invalidLocal")
      return
    }
    if (response.ok !== true) {
      root.error = String(response.error || root.t("operationFailed"))
      return
    }
    root.message = response.message || null
  }

  property Process process: Process {
    command: ["/usr/bin/python3", root.runnerPath, "read-message"]
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(value) { root.responseLine = String(value || "") }
    }
    onStarted: {
      if (!root.pendingRequest) {
        process.signal(15)
        return
      }
      process.write(JSON.stringify(root.pendingRequest) + "\n")
      root.pendingRequest = null
      process.stdinEnabled = false
    }
    onExited: function(exitCode) {
      if (root.responseLine !== "") root.applyResponse(root.responseLine)
      else root.error = root.t("noLocalResponse")
    }
  }
}
