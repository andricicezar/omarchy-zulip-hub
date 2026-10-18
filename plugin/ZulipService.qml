import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})
  property var state: Model.emptyState("")
  property bool loaded: false
  property string statePath: defaultStatePath()

  readonly property bool connected: state.connected === true
  readonly property string lastError: String(state.error || "")
  readonly property var unread: state.unread || ({ total: 0, mentions: 0, private: 0 })
  readonly property var recent: state.recent || []
  readonly property var accounts: state.accounts || []
  readonly property bool multiAccount: accounts.length > 1
  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 2, 1, 60)
  readonly property string statusText: Model.statusText(state, loaded)

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, minimum, maximum) {
    var number = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(number)) number = fallback
    return Math.max(minimum, Math.min(maximum, number))
  }

  function defaultStatePath() {
    var base = Quickshell.env("XDG_STATE_HOME")
    if (!base) base = Quickshell.env("HOME") + "/.local/state"
    return base + "/zulip-hub/state.json"
  }

  function load(raw) {
    var parsed = Model.parseState(raw)
    state = parsed.state
    loaded = parsed.ok
  }

  function refresh() {
    stateFile.reload()
  }

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    atomicWrites: true
    printErrors: false
    onLoaded: root.load(text())
    onLoadFailed: root.load("")
    onFileChanged: reload()
  }

  // FileView cannot watch a file that does not exist yet. This fallback also
  // recovers if the state directory is created after Omarchy Shell starts.
  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }
}
