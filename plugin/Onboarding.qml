import QtQuick
import Quickshell.Io

QtObject {
  id: root

  readonly property bool available: true
  property bool ready: false
  property bool configured: false
  property bool active: false
  property bool busy: process.running
  property bool editing: false
  property string site: ""
  property string email: ""
  property string service: "unknown"
  property string message: ""
  property string error: ""
  property var pendingRequest: null
  property string responseLine: ""
  property string activeAction: ""
  property bool settingsOpen: false
  property bool diagnosticsOpen: false
  property var settings: ({})
  property var diagnostics: ({})
  property var accounts: []
  property int maxAccounts: 16
  // Serveur du compte en cours de modification ; vide pour un nouveau compte.
  property string editingSite: ""
  readonly property string runnerPath: decodeURIComponent(
    String(Qt.resolvedUrl("../run-plugin.py")).replace(/^file:\/\//, ""))

  signal completed(string action)

  function request(payload) {
    if (process.running) return false
    root.error = ""
    root.message = ""
    root.responseLine = ""
    root.pendingRequest = payload
    root.activeAction = String(payload.action || "status")
    process.stdinEnabled = true
    process.command = ["/usr/bin/python3", root.runnerPath, "onboarding"]
    process.running = true
    return true
  }

  function refresh() { request({ action: "status" }) }

  function setup(siteValue, emailValue, apiKeyValue, nameValue, originalSite) {
    return request({
      action: "setup",
      site: String(siteValue || ""),
      email: String(emailValue || ""),
      api_key: String(apiKeyValue || ""),
      name: String(nameValue || ""),
      original_site: String(originalSite || "")
    })
  }

  function removeAccount(siteValue) {
    return request({ action: "remove_account", site: String(siteValue || "") })
  }

  function deactivate() { request({ action: "deactivate" }) }
  function activate() { request({ action: "activate" }) }
  function disconnect() { request({ action: "disconnect" }) }
  function loadDiagnostics() { request({ action: "diagnostics" }) }
  function reconnect() { request({ action: "reconnect" }) }
  function saveSettings(values) {
    var payload = { action: "save_settings" }
    for (var key in values) payload[key] = values[key]
    return request(payload)
  }

  function applyResponse(raw) {
    var response
    try {
      response = JSON.parse(String(raw || ""))
    } catch (exception) {
      root.error = "Réponse locale invalide."
      root.ready = true
      return
    }
    if (response.ok !== true) {
      root.error = String(response.error || "L’opération a échoué.")
      root.ready = true
      return
    }
    if (root.activeAction === "diagnostics" || root.activeAction === "reconnect") {
      root.diagnostics = response
      root.message = String(response.message || "")
      root.ready = true
      return
    }
    root.configured = response.configured === true
    root.active = response.active === true
    root.site = String(response.site || "")
    root.email = String(response.email || "")
    root.service = String(response.service || "unknown")
    root.message = String(response.message || "")
    if (response.settings && typeof response.settings === "object")
      root.settings = response.settings
    root.accounts = Array.isArray(response.accounts) ? response.accounts : []
    root.maxAccounts = Number(response.max_accounts) || 16
    root.ready = true
    if (root.configured && root.active) root.editing = false
  }

  property Process process: Process {
    command: ["/usr/bin/python3", root.runnerPath, "onboarding"]
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
      var action = root.activeAction
      if (root.responseLine !== "") root.applyResponse(root.responseLine)
      else {
        root.error = exitCode === 0
          ? "Le service local n’a renvoyé aucune réponse."
          : "Impossible de joindre le service local Zulip Hub."
        root.ready = true
      }
      root.completed(action)
      root.activeAction = ""
    }
  }
}
