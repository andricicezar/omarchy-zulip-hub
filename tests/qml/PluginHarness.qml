import QtQuick
import Quickshell

ShellRoot {
  readonly property bool expectOffline: Quickshell.env("ZULIP_HUB_EXPECT_OFFLINE") === "1"

  Loader {
    id: pluginLoader
    source: Qt.resolvedUrl("plugins/io.github.valentin-briez-banuls.zulip-hub/plugin/Panel.qml")
    onStatusChanged: {
      if (status === Loader.Error) {
        console.error("ZULIP_HUB_PLUGIN_LOAD_ERROR")
        Qt.exit(1)
      }
    }
  }

  Loader {
    id: serviceLoader
    source: Qt.resolvedUrl("plugins/io.github.valentin-briez-banuls.zulip-hub/plugin/Service.qml")
    onStatusChanged: if (status === Loader.Error) {
      console.error("ZULIP_HUB_SERVICE_LOAD_ERROR")
      Qt.exit(1)
    }
  }

  Timer {
    interval: 1500
    running: true
    onTriggered: {
      if (pluginLoader.status !== Loader.Ready) {
        console.error("ZULIP_HUB_PLUGIN_NOT_READY")
        Qt.exit(1)
        return
      }
      if (serviceLoader.status !== Loader.Ready) {
        console.error("ZULIP_HUB_SERVICE_NOT_READY")
        Qt.exit(1)
        return
      }
      var plugin = pluginLoader.item
      if (!plugin.onboardingController || plugin.onboardingController.available !== true) {
        console.error("ZULIP_HUB_ONBOARDING_NOT_READY")
        Qt.exit(1)
        return
      }
      plugin.onboardingController.ready = true
      plugin.onboardingController.configured = true
      plugin.onboardingController.settingsOpen = true
      if (!plugin.showSettings) {
        console.error("ZULIP_HUB_SETTINGS_NOT_READY")
        Qt.exit(1)
        return
      }
      plugin.onboardingController.settingsOpen = false
      plugin.onboardingController.diagnosticsOpen = true
      if (!plugin.showDiagnostics) {
        console.error("ZULIP_HUB_DIAGNOSTICS_NOT_READY")
        Qt.exit(1)
        return
      }
      plugin.onboardingController.diagnosticsOpen = false
      if (!plugin.composerController || plugin.composerController.available !== true) {
        console.error("ZULIP_HUB_COMPOSER_NOT_READY")
        Qt.exit(1)
        return
      }
      plugin.composerController.loaded = true
      plugin.composerController.users = [
        {id: 7, full_name: "Alice", email: "alice@example.com"},
        {id: 8, full_name: "Bob", email: "bob@example.com"}
      ]
      plugin.composerController.recentUserIds = [8]
      plugin.composerController.selectedIds = [7, 8]
      plugin.composerController.content = "Brouillon"
      plugin.composerController.open = true
      if (!plugin.showComposer || plugin.composerController.selectedUsers().length !== 2
          || plugin.composerController.filteredUsers()[0].id !== 8) {
        console.error("ZULIP_HUB_COMPOSER_STATE_MISMATCH")
        Qt.exit(1)
        return
      }
      plugin.composerController.open = false
      if (plugin.composerController.content !== "Brouillon") {
        console.error("ZULIP_HUB_DRAFT_NOT_PRESERVED")
        Qt.exit(1)
        return
      }
      plugin.composerController.startReply({
        id: 43, type: "stream", channel: "backend", topic: "deployment", sender: "Alice"
      })
      if (!plugin.showComposer || plugin.composerController.replying !== true
          || plugin.composerController.replyDestination !== "#backend  \u203a  deployment") {
        console.error("ZULIP_HUB_REPLY_STATE_MISMATCH")
        Qt.exit(1)
        return
      }
      plugin.composerController.startReply({
        id: 42, type: "private", sender: "Bob Durand"
      })
      if (plugin.composerController.replyDestination !== "Bob Durand") {
        console.error("ZULIP_HUB_REPLY_DIRECT_MISMATCH")
        Qt.exit(1)
        return
      }
      plugin.composerController.startCompose()
      if (plugin.composerController.replying !== false) {
        console.error("ZULIP_HUB_REPLY_NOT_CLEARED")
        Qt.exit(1)
        return
      }
      plugin.composerController.open = false
      if (!plugin.readerController || plugin.readerController.available !== true) {
        console.error("ZULIP_HUB_READER_NOT_READY")
        Qt.exit(1)
        return
      }
      plugin.readerController.message = {
        id: 42, type: "stream", channel: "backend", topic: "deployment",
        sender: "Alice Martin", content: "Bonjour"
      }
      plugin.readerController.open = true
      if (!plugin.showMessage
          || plugin.readerController.destination !== "#backend  \u203a  deployment") {
        console.error("ZULIP_HUB_READER_STATE_MISMATCH")
        Qt.exit(1)
        return
      }
      plugin.readerController.close()
      if (plugin.showMessage || plugin.readerController.message !== null) {
        console.error("ZULIP_HUB_READER_CONTENT_KEPT")
        Qt.exit(1)
        return
      }
      if (!plugin.updateController || plugin.updateController.available !== true) {
        console.error("ZULIP_HUB_UPDATE_NOT_READY")
        Qt.exit(1)
        return
      }
      plugin.updateController.runningVersion = "2.2.1"
      plugin.updateController.installedVersion = "2.2.1"
      if (plugin.updateController.restartNeeded) {
        console.error("ZULIP_HUB_RESTART_FALSE_ALARM")
        Qt.exit(1)
        return
      }
      plugin.updateController.installedVersion = "2.3.0"
      if (!plugin.updateController.restartNeeded) {
        console.error("ZULIP_HUB_RESTART_NOT_DETECTED")
        Qt.exit(1)
        return
      }
      plugin.updateController.installedVersion = "2.2.1"
      if (plugin.updateController.restartNeeded) {
        console.error("ZULIP_HUB_RESTART_STUCK")
        Qt.exit(1)
        return
      }
      plugin.onboardingController.accounts = [
        {site: "https://a.example.org", label: "A", name: "", email: "me@a.example.org", has_secret: true},
        {site: "https://b.example.org", label: "B", name: "B", email: "me@b.example.org", has_secret: true},
        {site: "https://c.example.org", label: "C", name: "", email: "me@c.example.org", has_secret: false}
      ]
      if (plugin.composerAccounts().length !== 2) {
        console.error("ZULIP_HUB_COMPOSER_ACCOUNTS_MISMATCH")
        Qt.exit(1)
        return
      }
      plugin.beginAccountEdit(plugin.onboardingController.accounts[1])
      if (!plugin.showSetup || plugin.onboardingController.editingSite !== "https://b.example.org") {
        console.error("ZULIP_HUB_ACCOUNT_EDIT_MISMATCH")
        Qt.exit(1)
        return
      }
      plugin.onboardingController.editing = false
      plugin.composerController.startReply({
        id: 42, type: "private", sender: "Bob", site: "https://b.example.org", account: "B"
      })
      if (plugin.composerController.site !== "https://b.example.org"
          || plugin.composerController.loaded !== false) {
        console.error("ZULIP_HUB_REPLY_SITE_MISMATCH")
        Qt.exit(1)
        return
      }
      plugin.composerController.open = false
      if (expectOffline) {
        if (plugin.hub.loaded || plugin.hub.connected || plugin.hub.statusText === "") {
          console.error("ZULIP_HUB_OFFLINE_STATE_MISMATCH")
          Qt.exit(1)
          return
        }
        console.log("ZULIP_HUB_PLUGIN_OFFLINE_READY")
        Qt.exit(0)
        return
      }
      if (!plugin.hub.loaded || !plugin.hub.connected) {
        console.error("ZULIP_HUB_STATE_NOT_LOADED")
        Qt.exit(1)
        return
      }
      if (plugin.hub.unread.total !== 5 || plugin.hub.unread.mentions !== 2
          || plugin.hub.unread.private !== 1 || plugin.messageCount !== 2
          || plugin.hasMentions !== true) {
        console.error("ZULIP_HUB_STATE_MISMATCH")
        Qt.exit(1)
        return
      }
      console.log("ZULIP_HUB_PLUGIN_READY")
      Qt.exit(0)
    }
  }
}
