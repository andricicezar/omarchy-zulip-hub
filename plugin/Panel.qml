import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "." as ZulipHub
import "Model.js" as Model
import "I18n.js" as I18n

Panel {
  id: root
  moduleName: "io.github.valentin-briez-banuls.zulip-hub"
  ipcTarget: "io.github.valentin-briez-banuls.zulip-hub"
  manageIpc: false

  property int selectedIndex: 0
  property bool cursorActive: false
  property double nowMilliseconds: Date.now()
  property string actionError: ""
  property string actionMessage: ""
  property bool osIntegrationInstalled: false
  // Saisie manuelle de la clé API : repli quand ni navigateur ni mot de passe
  // ne conviennent.
  property bool useApiKey: false
  property string osResponseLine: ""
  readonly property string runnerPath: decodeURIComponent(
    String(Qt.resolvedUrl("../run-plugin.py")).replace(/^file:\/\//, ""))
  readonly property string localeName: Qt.locale().name
  readonly property var onboarding: onboardingLoader.item || fallbackOnboarding
  readonly property var composer: composerLoader.item || fallbackComposer
  readonly property var reader: readerLoader.item || fallbackReader
  readonly property var updates: updateLoader.item || fallbackUpdate
  readonly property bool showSetup: onboarding.ready
    && (!onboarding.configured || onboarding.editing)
  readonly property bool showSettings: onboarding.ready && onboarding.configured
    && onboarding.settingsOpen && !root.showSetup && !root.showComposer && !root.showMessage
  readonly property bool showDiagnostics: onboarding.ready && onboarding.configured
    && onboarding.diagnosticsOpen && !root.showSetup && !root.showSettings
    && !root.showComposer && !root.showMessage
  readonly property bool showComposer: onboarding.ready && onboarding.configured
    && composer.open && !root.showSetup
  readonly property bool showMessage: onboarding.ready && onboarding.configured
    && reader.open && !root.showSetup && !root.showComposer
  // Vue par defaut : la liste. La condition etait recopiee six fois.
  readonly property bool showHome: !root.showSetup && !root.showSettings
    && !root.showDiagnostics && !root.showComposer && !root.showMessage
  readonly property var onboardingController: onboarding
  readonly property var composerController: composer
  readonly property var readerController: reader
  readonly property var updateController: updates
  property var fallbackUpdate: ({
    available: false, restartNeeded: false, restarting: false,
    runningVersion: "", installedVersion: "", restartShell: function() {}
  })
  property var fallbackReader: ({
    available: false, open: false, busy: false, message: null, error: "", destination: "",
    show: function() { return false }, close: function() {}
  })
  property var fallbackOnboarding: ({
    available: false, ready: false, configured: false, active: false, busy: false, editing: false,
    settingsOpen: false, diagnosticsOpen: false, settings: ({}), diagnostics: ({}),
    site: "", email: "", service: "unknown", message: "", error: "",
    accounts: [], maxAccounts: 16, editingSite: "",
    methods: [], methodsLoaded: false, passwordAvailable: false, realmName: "", loginPending: false,
    clearMethods: function() {}, loadMethods: function() { return false },
    startLogin: function() { return false }, passwordLogin: function() { return false },
    cancelLogin: function() {}, importZuliprc: function() {},
    refresh: function() {}, setup: function() { return false }, removeAccount: function() { return false },
    activate: function() {}, deactivate: function() {}, disconnect: function() {},
    saveSettings: function() { return false }, loadDiagnostics: function() {}, reconnect: function() {}
  })
  property var fallbackComposer: ({
    available: false, open: false, loaded: false, busy: false, users: [], selectedIds: [],
    query: "", content: "", error: "", message: "", maxMessageLength: 10000,
    replying: false, replyTarget: null, replyDestination: "", site: "",
    loadDirectory: function() {}, selectSite: function() {}, filteredUsers: function() { return [] },
    selectedUsers: function() { return [] }, isSelected: function() { return false },
    toggleUser: function() {}, send: function() { return false },
    startReply: function() {}, startCompose: function() {}
  })

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool showTotal: setting("showTotal", true) === true
  readonly property bool showMentions: setting("showMentions", true) === true
  readonly property bool hideWhenEmpty: setting("hideWhenEmpty", false) === true
  readonly property bool hasMentions: hub.unread.mentions > 0
  readonly property int messageCount: hub.recent.length
  readonly property var hub: hubLoader.item || fallbackHub
  property var fallbackHub: ({
    state: Model.emptyState(""),
    loaded: false,
    connected: false,
    lastError: "",
    unread: { total: 0, mentions: 0, private: 0 },
    recent: [],
    accounts: [],
    multiAccount: false,
    statusText: "Waiting for bridge state",
    refresh: function() {}
  })

  function refresh() { hub.refresh() }
  function t(key) { return I18n.text(key, root.localeName) }
  readonly property string localizedStatus: !hub.loaded ? t("waiting")
    : (hub.connected ? t("connected") : (hub.lastError !== "" ? hub.lastError : t("offline")))

  function splitChannels(value) {
    var rows = String(value || "").split(",")
    var result = []
    for (var index = 0; index < rows.length; index++) {
      var channel = rows[index].trim()
      if (channel !== "" && result.indexOf(channel) === -1) result.push(channel)
    }
    return result
  }

  // Avec plusieurs comptes, chaque conversation dit de quel serveur elle vient.
  function accountPrefix(row) {
    return hub.multiAccount && row && row.account ? String(row.account) + "  ·  " : ""
  }

  function siteArguments(row) {
    return row && row.site ? ["--site", String(row.site)] : []
  }

  // Les champs du formulaire sont remplis ici, et non par liaison : une
  // liaison rompue a la premiere frappe ne suivrait plus le compte choisi.
  function fillAccountForm(account) {
    onboarding.editingSite = account ? String(account.site || "") : ""
    siteField.text = account ? String(account.site || "") : ""
    emailField.text = account ? String(account.email || "") : ""
    nameField.text = account ? String(account.name || "") : ""
    apiKeyField.text = ""
    passwordField.text = ""
    root.useApiKey = false
    onboarding.clearMethods()
    // Un compte existant : ses options de connexion sont cherchées d’emblée.
    if (account && account.site) Qt.callLater(function() { onboarding.loadMethods(account.site) })
  }

  function beginAccountEdit(account) {
    fillAccountForm(account)
    onboarding.settingsOpen = false
    onboarding.editing = true
  }

  function composerAccounts() {
    return onboarding.accounts.filter(function(account) { return account.has_secret === true })
  }

  function ensureCursor() {
    var rows = focusTargets()
    selectedIndex = rows.length === 0 ? 0 : Math.max(0, Math.min(rows.length - 1, selectedIndex))
  }

  function moveCursor(delta) {
    cursorActive = true
    var rows = focusTargets()
    if (rows.length === 0) return
    selectedIndex = Math.max(0, Math.min(rows.length - 1, selectedIndex + delta))
    scrollCursorIntoView()
  }

  function appendTarget(rows, item) {
    if (item && item.visible && item.enabled !== false) rows.push(item)
  }

  function appendChildren(rows, container) {
    if (!container) return
    for (var index = 0; index < container.children.length; index++) {
      var item = container.children[index]
      if (item && typeof item.keyboardActivate === "function" && item.visible && item.enabled !== false)
        rows.push(item)
    }
  }

  function focusTargets() {
    var rows = []
    if (root.showSetup) {
      appendTarget(rows, nameField); appendTarget(rows, siteField); appendTarget(rows, findMethodsButton)
      appendChildren(rows, methodList)
      appendTarget(rows, emailField); appendTarget(rows, passwordField); appendTarget(rows, passwordButton)
      appendTarget(rows, apiKeyField); appendTarget(rows, connectButton)
      appendTarget(rows, cancelLoginButton); appendTarget(rows, apiKeyToggle)
      appendTarget(rows, disconnectButton); appendTarget(rows, importButton)
    } else if (root.showComposer) {
      appendTarget(rows, composeNavButton)
      if (!composer.replying) {
        appendTarget(rows, composeAccountField)
        appendTarget(rows, composeSearchField)
        appendChildren(rows, recipientResults)
      }
      appendTarget(rows, composeField); appendTarget(rows, sendButton)
      if (!composer.replying) appendTarget(rows, refreshDirectoryButton)
    } else if (root.showMessage) {
      appendTarget(rows, readerBackButton); appendTarget(rows, readerReplyButton)
      appendTarget(rows, readerOpenButton)
    } else if (root.showSettings) {
      appendTarget(rows, settingsNavButton)
      appendTarget(rows, notifyEnabled); appendTarget(rows, notifyPrivate); appendTarget(rows, notifyMentions)
      appendTarget(rows, notifyFollowed); appendTarget(rows, notifyOther); appendTarget(rows, notifyLocked)
      appendTarget(rows, groupWindowField); appendTarget(rows, mutedChannelsField); appendTarget(rows, alwaysChannelsField)
      appendTarget(rows, openModeField); appendTarget(rows, launchersToggle); appendTarget(rows, desktopCommandField); appendTarget(rows, workspaceCommandField)
      appendTarget(rows, saveSettingsButton)
      appendChildren(rows, accountList); appendTarget(rows, addAccountButton)
      appendTarget(rows, diagnosticsButton); appendTarget(rows, serviceButton)
      appendTarget(rows, osIntegrationButton)
    } else if (root.showDiagnostics) {
      appendTarget(rows, diagnosticsBackButton); appendTarget(rows, diagnosticsRefreshButton); appendTarget(rows, reconnectButton)
    } else {
      appendTarget(rows, composeNavButton); appendTarget(rows, settingsNavButton)
      if (updates.restartNeeded) appendTarget(rows, restartShellButton)
      appendChildren(rows, messageColumn)
    }
    return rows
  }

  function cursorTarget() {
    var rows = focusTargets()
    return selectedIndex >= 0 && selectedIndex < rows.length ? rows[selectedIndex] : null
  }

  function hasCursor(item) { return cursorActive && cursorTarget() === item }

  function selectTarget(item) {
    var index = focusTargets().indexOf(item)
    if (index < 0) return
    cursorActive = true
    selectedIndex = index
  }

  function activateCursor() {
    var item = cursorTarget()
    if (item && typeof item.keyboardActivate === "function") item.keyboardActivate()
  }

  function leaveEditor() {
    keyCatcher.forceActiveFocus()
    cursorActive = true
  }

  function resetCursor() {
    selectedIndex = 0
    cursorActive = false
    panelFlick.contentY = 0
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function scrollCursorIntoView() {
    var item = cursorTarget()
    if (!item) return
    Qt.callLater(function() {
      if (!item) return
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var margin = Style.space(6)
      var top = point.y
      var bottom = top + item.height
      if (top < panelFlick.contentY + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > panelFlick.contentY + panelFlick.height - margin)
        panelFlick.contentY = Math.min(
          Math.max(0, panelFlick.contentHeight - panelFlick.height),
          bottom + margin - panelFlick.height
        )
    })
  }

  function selectRow(index) {
    var rows = focusTargets()
    for (var targetIndex = 0; targetIndex < rows.length; targetIndex++) {
      if (rows[targetIndex].rowIndex === index) {
        cursorActive = true
        selectedIndex = targetIndex
        return
      }
    }
  }

  function selectedMessage() {
    var item = cursorTarget()
    return item && item.row ? item.row : null
  }

  function openInZulip(row) {
    if (!row || openProcess.running) return
    actionError = ""
    openProcess.command = ["/usr/bin/python3", root.runnerPath, "open-message", String(row.id)]
      .concat(root.siteArguments(row))
    openProcess.running = true
    close()
  }

  function readMessage(row) {
    if (!row || reader.available !== true) return
    root.actionError = ""
    root.actionMessage = ""
    onboarding.settingsOpen = false
    onboarding.diagnosticsOpen = false
    reader.show(row)
  }

  function replyToMessage(row) {
    if (!row || composer.available !== true) return
    root.actionError = ""
    root.actionMessage = ""
    onboarding.settingsOpen = false
    onboarding.diagnosticsOpen = false
    composer.startReply(row)
    // L annuaire porte aussi la limite de taille du serveur ; il est mis en cache.
    composer.loadDirectory(false)
  }

  function markMessageRead(row) {
    if (!row || readProcess.running) return
    actionError = ""
    readProcess.command = ["/usr/bin/python3", root.runnerPath, "mark-read", String(row.id)]
      .concat(root.siteArguments(row))
    readProcess.running = true
  }

  function toggleWorkspace() {
    if (workspaceProcess.running) return
    workspaceProcess.command = ["/usr/bin/python3", root.runnerPath, "workspace-toggle"]
    workspaceProcess.running = true
  }

  function osIntegration(action) {
    if (osProcess.running) return
    root.actionError = ""
    root.actionMessage = ""
    root.osResponseLine = ""
    osProcess.command = ["/usr/bin/python3", root.runnerPath, "os-integration", action]
    osProcess.running = true
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  visible: !hideWhenEmpty || hub.unread.total > 0 || !hub.connected

  onOpenedChanged: if (opened) {
    cursorActive = false
    panelFlick.contentY = 0
    hub.refresh()
    onboarding.refresh()
    root.osIntegration("status")
    ensureCursor()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }
  onMessageCountChanged: ensureCursor()
  onSelectedIndexChanged: scrollCursorIntoView()
  onShowSetupChanged: {
    // Premier lancement, ou clé perdue : le formulaire part du premier compte.
    if (showSetup && !onboarding.editing) fillAccountForm(onboarding.accounts[0] || null)
    resetCursor()
  }
  onShowSettingsChanged: resetCursor()
  onShowDiagnosticsChanged: resetCursor()
  onShowComposerChanged: resetCursor()

  onSettingsChanged: if (hubLoader.item) hubLoader.item.settings = root.settings

  Loader {
    id: hubLoader
    active: true
    visible: false
    source: Qt.resolvedUrl("ZulipService.qml")
    onLoaded: item.settings = root.settings
  }

  Loader {
    id: onboardingLoader
    active: true
    visible: false
    source: Qt.resolvedUrl("Onboarding.qml")
  }

  Loader {
    id: composerLoader
    active: true
    visible: false
    source: Qt.resolvedUrl("Composer.qml")
  }

  Loader {
    id: readerLoader
    active: true
    visible: false
    source: Qt.resolvedUrl("Reader.qml")
  }

  Loader {
    id: updateLoader
    active: true
    visible: false
    source: Qt.resolvedUrl("Update.qml")
  }

  Connections {
    target: composerLoader.item
    ignoreUnknownSignals: true
    function onSent(messageId) {
      root.actionError = ""
      root.actionMessage = root.t("sent")
      hub.refresh()
    }
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { hub.refresh(); return "ok" }
    function workspaceToggle(): void { root.toggleWorkspace() }
    function status(): string { return hub.statusText }
    function onboardingStatus(): string {
      return JSON.stringify({
        ready: onboarding.ready,
        configured: onboarding.configured,
        active: onboarding.active,
        service: onboarding.service,
        error: onboarding.error
      })
    }
    function version(): string { return "2.0.0" }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component {
      ZulipHub.ZulipIcon {
        anchors.centerIn: parent
        iconSize: Math.min(parent.width, parent.height)
        color: root.foreground
        connected: hub.connected
        urgent: root.showMentions && root.hasMentions
      }
    }
    active: root.showMentions && root.hasMentions
    dimmed: !hub.connected
    fontSize: Style.font.body
    tooltipText: "Zulip — " + root.localizedStatus
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) hub.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: siteField.activeFocus || emailField.activeFocus || apiKeyField.activeFocus
        || nameField.activeFocus || composeAccountField.activeFocus || passwordField.activeFocus
        || composeSearchField.activeFocus || composeField.activeFocus
        || groupWindowField.activeFocus || mutedChannelsField.activeFocus
        || alwaysChannelsField.activeFocus || desktopCommandField.activeFocus
        || workspaceCommandField.activeFocus || openModeField.activeFocus
      onMoveRequested: function(dx, dy) { root.moveCursor(dy !== 0 ? dy : dx) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: {
        if (root.showComposer) composer.open = false
        else if (root.showMessage) reader.close()
        else if (root.showSettings || root.showDiagnostics || (root.showSetup && onboarding.configured)) {
          onboarding.editing = false; onboarding.settingsOpen = false; onboarding.diagnosticsOpen = false
        } else root.close()
      }
      onDeleteRequested: {
        var row = root.selectedMessage()
        if (row) root.markMessageRead(row)
      }
      onTabRequested: function(direction) { root.moveCursor(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") hub.refresh()
        else if (text === "a" || text === "A") root.replyToMessage(root.selectedMessage())
        else if (text === "o" || text === "O") root.openInZulip(root.selectedMessage())
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: contentColumn
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: "Zulip"
            meta: root.showSetup ? root.t("configuration") : root.localizedStatus
            detail: root.showSetup
              ? root.t("secureConnection")
              : (hub.connected ? String(hub.unread.total) + " " + root.t("unread") : root.t("offline"))
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: hub.connected ? 1.0 : 0.45
            iconComponent: Component {
              Item {
                implicitWidth: Style.space(34)
                implicitHeight: Style.space(34)
                ZulipHub.ZulipIcon { anchors.centerIn: parent; iconSize: Style.space(30); color: root.foreground; connected: hub.connected; urgent: root.hasMentions }
              }
            }
          }

          RowLayout {
            width: parent.width
            spacing: Style.space(8)

            Button {
              id: composeNavButton
              visible: onboarding.configured && !root.showSetup
              text: root.showComposer ? root.t("messages") : root.t("compose")
              enabled: onboarding.ready && !composer.busy
              hasCursor: root.hasCursor(this)
              focusable: true
              function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              onClicked: {
                if (root.showComposer) {
                  composer.open = false
                } else {
                  root.actionMessage = ""
                  onboarding.settingsOpen = false
                  onboarding.diagnosticsOpen = false
                  composer.startCompose()
                  composer.loadDirectory(false)
                }
              }
            }
            Button {
              id: settingsNavButton
              visible: onboarding.configured && !root.showComposer
              text: root.showSetup
                ? root.t("returnSettings")
                : (root.showSettings || root.showDiagnostics ? root.t("messages") : root.t("settings"))
              enabled: onboarding.ready && !onboarding.busy
              hasCursor: root.hasCursor(this)
              focusable: true
              function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              onClicked: {
                if (root.showSetup) {
                  onboarding.editing = false
                  onboarding.settingsOpen = true
                } else if (root.showSettings || root.showDiagnostics) {
                  onboarding.settingsOpen = false
                  onboarding.diagnosticsOpen = false
                } else {
                  onboarding.settingsOpen = true
                }
              }
            }
            Item { Layout.fillWidth: true }
            Text {
              text: onboarding.active ? root.t("active") : String(onboarding.service).toUpperCase()
              color: onboarding.active ? root.foreground : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }
          }

          Column {
            id: setupColumn
            visible: root.showSetup
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: !onboarding.configured ? root.t("welcome")
                : (onboarding.editingSite !== "" ? root.t("account") : root.t("newAccount"))
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              text: root.t("secureKey")
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            TextField {
              id: nameField
              width: parent.width
              placeholderText: root.t("accountName")
              enabled: !onboarding.busy
              Accessible.name: root.t("accountName")
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }

            TextField {
              id: siteField
              width: parent.width
              placeholderText: "https://zulip.votre-entreprise.fr"
              inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoAutoUppercase
              enabled: !onboarding.busy && !onboarding.loginPending
              Accessible.name: "URL du serveur Zulip"
              // Les méthodes affichées sont celles du serveur saisi, pas d’un autre.
              onTextEdited: onboarding.clearMethods()
              onAccepted: findMethodsButton.clicked()
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }

            Button {
              id: findMethodsButton
              visible: !onboarding.methodsLoaded && !onboarding.loginPending
              width: parent.width
              text: onboarding.busy ? root.t("checking") : root.t("findMethods")
              enabled: !onboarding.busy && siteField.text.trim() !== ""
              onClicked: onboarding.loadMethods(siteField.text)
              hasCursor: root.hasCursor(this)
              focusable: true
              function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
            }

            Column {
              id: methodList
              visible: onboarding.methodsLoaded && !onboarding.loginPending
              width: parent.width
              spacing: Style.space(6)
              Repeater {
                model: onboarding.methods
                Button {
                  required property var modelData
                  width: methodList.width
                  text: root.t("signInWith").replace("%1", String(modelData.display_name || modelData.name))
                  enabled: !onboarding.busy
                  onClicked: onboarding.startLogin(siteField.text, modelData.name,
                                                   nameField.text, onboarding.editingSite)
                  hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
                  onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
                }
              }
            }

            TextField {
              id: emailField
              visible: !onboarding.loginPending
                && ((onboarding.methodsLoaded && onboarding.passwordAvailable) || root.useApiKey)
              width: parent.width
              placeholderText: "prenom.nom@entreprise.fr"
              inputMethodHints: Qt.ImhEmailCharactersOnly | Qt.ImhNoAutoUppercase
              enabled: !onboarding.busy
              Accessible.name: "Adresse e-mail Zulip"
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }

            TextField {
              id: passwordField
              visible: !onboarding.loginPending && !root.useApiKey
                && onboarding.methodsLoaded && onboarding.passwordAvailable
              width: parent.width
              placeholderText: root.t("passwordPlaceholder")
              echoMode: TextInput.Password
              passwordCharacter: "●"
              enabled: !onboarding.busy
              Accessible.name: root.t("passwordPlaceholder")
              onAccepted: passwordButton.clicked()
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }

            Button {
              id: passwordButton
              visible: passwordField.visible
              width: parent.width
              text: onboarding.busy ? root.t("checking") : root.t("passwordSignIn")
              enabled: !onboarding.busy && emailField.text.trim() !== "" && passwordField.text !== ""
              onClicked: {
                var secret = passwordField.text
                if (onboarding.passwordLogin(siteField.text, emailField.text, secret,
                                             nameField.text, onboarding.editingSite)) {
                  passwordField.text = ""
                  secret = ""
                }
              }
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
            }

            TextField {
              id: apiKeyField
              visible: root.useApiKey && !onboarding.loginPending
              width: parent.width
              placeholderText: onboarding.editingSite !== ""
                ? "Nouvelle clé API (requise pour retester)"
                : "Clé API Zulip"
              echoMode: TextInput.Password
              passwordCharacter: "●"
              enabled: !onboarding.busy
              Accessible.name: "Clé API Zulip"
              onAccepted: connectButton.clicked()
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }

            Button {
              id: connectButton
              visible: apiKeyField.visible
              width: parent.width
              text: onboarding.busy ? root.t("checking") : root.t("testActivate")
              enabled: !onboarding.busy && siteField.text.trim() !== ""
                && emailField.text.trim() !== "" && apiKeyField.text.trim() !== ""
              onClicked: {
                var secret = apiKeyField.text
                if (onboarding.setup(siteField.text, emailField.text, secret,
                                     nameField.text, onboarding.editingSite)) {
                  apiKeyField.text = ""
                  secret = ""
                }
              }
              hasCursor: root.hasCursor(this)
              focusable: true
              function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
            }

            Text {
              visible: onboarding.loginPending
              width: parent.width
              text: root.t("waitingBrowser")
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Button {
              id: cancelLoginButton
              visible: onboarding.loginPending
              width: parent.width
              text: root.t("cancel")
              enabled: !onboarding.busy
              onClicked: onboarding.cancelLogin()
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
            }

            RowLayout {
              visible: !onboarding.loginPending
              width: parent.width
              spacing: Style.space(8)
              Button {
                id: apiKeyToggle
                text: root.useApiKey ? root.t("hideApiKey") : root.t("useApiKey")
                enabled: !onboarding.busy
                onClicked: root.useApiKey = !root.useApiKey
                hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
                onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              }
              Button {
                id: disconnectButton
                visible: onboarding.configured && onboarding.editingSite !== ""
                text: onboarding.accounts.length > 1 ? root.t("removeAccount") : root.t("disconnect")
                enabled: !onboarding.busy
                onClicked: onboarding.removeAccount(onboarding.editingSite)
                hasCursor: root.hasCursor(this)
                focusable: true
                function keyboardActivate() { clicked() }
                onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              }
            }

            Button {
              id: importButton
              visible: !onboarding.loginPending && onboarding.editingSite === ""
              width: parent.width
              text: root.t("importZuliprc")
              enabled: !onboarding.busy
              onClicked: onboarding.importZuliprc()
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
            }

            Text {
              visible: onboarding.error !== "" || onboarding.message !== ""
              width: parent.width
              text: onboarding.error !== "" ? onboarding.error : onboarding.message
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: onboarding.error !== "" ? root.urgent : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          Column {
            visible: root.showComposer
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: composer.replying ? root.t("replyTitle") : root.t("newDirect")
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              visible: composer.replying
              width: parent.width
              text: root.accountPrefix(composer.replyTarget) + composer.replyDestination
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            ComboBox {
              id: composeAccountField
              visible: !composer.replying && root.composerAccounts().length > 1
              width: parent.width
              model: root.composerAccounts().map(function(account) {
                return root.t("fromAccount") + " : " + account.label
              })
              currentIndex: {
                var accounts = root.composerAccounts()
                for (var index = 0; index < accounts.length; index++)
                  if (accounts[index].site === composer.site) return index
                return 0
              }
              enabled: !composer.busy
              onActivated: function(index) {
                var account = root.composerAccounts()[index]
                if (account) composer.selectSite(account.site, true)
              }
              property bool hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus(); popup.open() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
            }

            TextField {
              id: composeSearchField
              visible: !composer.replying
              width: parent.width
              placeholderText: composer.loaded ? root.t("searchPerson") : root.t("loadingDirectory")
              text: composer.query
              enabled: composer.loaded && !composer.busy
              onTextEdited: composer.query = text
              Accessible.name: "Rechercher un destinataire"
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true }
                else if (event.key === Qt.Key_Backspace && text === "" && composer.selectedIds.length > 0) {
                  var selected = composer.selectedIds.slice(); selected.pop(); composer.selectedIds = selected
                  event.accepted = true
                } else if (event.key === Qt.Key_Down) {
                  root.leaveEditor(); root.cursorActive = true; root.selectedIndex = 1; event.accepted = true
                }
              }
            }

            Flow {
              visible: !composer.replying
              width: parent.width
              spacing: Style.space(5)
              Repeater {
                model: composer.selectedUsers()
                Rectangle {
                  required property var modelData
                  implicitWidth: selectedLabel.implicitWidth + Style.space(14)
                  implicitHeight: selectedLabel.implicitHeight + Style.space(7)
                  radius: height / 2
                  color: Style.selectedFillFor(root.foreground, Color.accent)
                  Text {
                    id: selectedLabel
                    anchors.centerIn: parent
                    text: modelData.full_name + "  ×"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: composer.toggleUser(modelData.id)
                  }
                }
              }
            }

            Column {
              id: recipientResults
              visible: !composer.replying
              width: parent.width
              spacing: Style.space(4)
              Repeater {
                model: composer.filteredUsers()
                Button {
                  required property var modelData
                  width: parent.width
                  text: (composer.isSelected(modelData.id) ? "✓  " : "")
                    + modelData.full_name + (modelData.email ? " — " + modelData.email : "")
                  enabled: !composer.busy
                  onClicked: composer.toggleUser(modelData.id)
                  hasCursor: root.hasCursor(this)
                  focusable: true
                  function keyboardActivate() { clicked() }
                  onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
                }
              }
            }

            TextArea {
              id: composeField
              width: parent.width
              height: Style.space(110)
              placeholderText: root.t("messagePlaceholder")
              text: composer.content
              wrapMode: TextEdit.Wrap
              enabled: !composer.busy
              onTextChanged: composer.content = text
              Accessible.name: "Contenu du message"
              background: Rectangle {
                color: Style.controlFill(composeField.activeFocus, root.hasCursor(composeField), root.foreground, Color.accent)
                border.width: composeField.activeFocus || root.hasCursor(composeField) ? Math.max(1, Style.normalBorderWidth) : 0
                border.color: root.foreground
                radius: Style.cornerRadius
              }
              function keyboardActivate() { forceActiveFocus() }
              Keys.onPressed: function(event) {
                if ((event.modifiers & Qt.ControlModifier)
                    && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                  composer.send()
                  event.accepted = true
                } else if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true }
              }
            }

            RowLayout {
              width: parent.width
              Text {
                text: String(composer.content.length) + " / " + String(composer.maxMessageLength)
                color: composer.content.length > composer.maxMessageLength ? root.urgent : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Item { Layout.fillWidth: true }
              Button {
                id: sendButton
                text: composer.busy ? root.t("sending") : root.t("send")
                enabled: !composer.busy
                  && (composer.replying || composer.selectedIds.length > 0)
                  && composer.content.trim() !== ""
                  && composer.content.length <= composer.maxMessageLength
                onClicked: composer.send()
                hasCursor: root.hasCursor(this)
                focusable: true
                function keyboardActivate() { clicked() }
                onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              }
            }

            Button {
              id: refreshDirectoryButton
              visible: !composer.replying
              width: parent.width
              text: root.t("refreshDirectory")
              enabled: !composer.busy
              onClicked: composer.loadDirectory(true)
              hasCursor: root.hasCursor(this)
              focusable: true
              function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
            }

            Text {
              visible: composer.error !== "" || composer.message !== ""
              width: parent.width
              text: composer.error !== "" ? composer.error : composer.message
              wrapMode: Text.WordWrap
              color: composer.error !== "" ? root.urgent : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          Column {
            visible: root.showMessage
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: root.t("messageTitle")
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              text: reader.message ? String(reader.message.sender || "") : ""
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Text {
              width: parent.width
              text: root.accountPrefix(reader.message) + reader.destination
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Text {
              visible: reader.busy && reader.message === null
              width: parent.width
              text: root.t("loadingMessage")
              textFormat: Text.PlainText
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Text {
              visible: reader.message !== null
              width: parent.width
              // Contenu ecrit par un tiers : rendu en texte brut. Un rendu
              // enrichi laisserait lexpediteur piloter la mise en forme et le
              // chargement de ressources embarquees, sans politique possible.
              text: reader.message ? String(reader.message.content || "") : ""
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Text {
              visible: reader.error !== ""
              width: parent.width
              text: reader.error
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            RowLayout {
              width: parent.width
              spacing: Style.space(8)

              Button {
                id: readerBackButton
                text: root.t("backMessages")
                onClicked: reader.close()
                hasCursor: root.hasCursor(this)
                focusable: true
                function keyboardActivate() { clicked() }
                onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              }

              Button {
                id: readerReplyButton
                text: root.t("reply")
                enabled: composer.available === true && !composer.busy && reader.message !== null
                onClicked: root.replyToMessage(reader.message)
                hasCursor: root.hasCursor(this)
                focusable: true
                function keyboardActivate() { clicked() }
                onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              }

              Button {
                id: readerOpenButton
                text: root.t("openInZulip")
                enabled: !openProcess.running && reader.message !== null
                onClicked: root.openInZulip(reader.message)
                hasCursor: root.hasCursor(this)
                focusable: true
                function keyboardActivate() { clicked() }
                onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
              }
            }
          }

          Column {
            visible: root.showSettings
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader { text: root.t("notifications"); foreground: root.foreground; fontFamily: root.fontFamily }
            Toggle {
              id: notifyEnabled
              width: parent.width
              label: root.t("enableNotifications")
              checked: onboarding.settings.notifications_enabled !== false
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { clicked() }
              onClicked: checked = !checked
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            Toggle {
              id: notifyPrivate
              width: parent.width
              label: root.t("directMessages")
              checked: onboarding.settings.private_messages !== false
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { clicked() }
              onClicked: checked = !checked
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            Toggle {
              id: notifyMentions
              width: parent.width
              label: root.t("mentions")
              checked: onboarding.settings.mentions !== false
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { clicked() }
              onClicked: checked = !checked
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            Toggle {
              id: notifyFollowed
              width: parent.width
              label: root.t("followedTopics")
              checked: onboarding.settings.followed_topics !== false
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { clicked() }
              onClicked: checked = !checked
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            Toggle {
              id: notifyOther
              width: parent.width
              label: root.t("otherMessages")
              checked: onboarding.settings.other_messages === true
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { clicked() }
              onClicked: checked = !checked
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            Toggle {
              id: notifyLocked
              width: parent.width
              label: root.t("hideLocked")
              checked: onboarding.settings.hide_content_when_locked !== false
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { clicked() }
              onClicked: checked = !checked
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }

            TextField {
              id: groupWindowField
              width: parent.width
              placeholderText: root.t("groupSeconds")
              text: String(onboarding.settings.group_window_seconds || 10)
              validator: IntValidator { bottom: 1; top: 300 }
              inputMethodHints: Qt.ImhDigitsOnly
              hasCursor: root.hasCursor(this); function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }
            TextField {
              id: mutedChannelsField
              width: parent.width
              placeholderText: root.t("mutedChannels")
              text: (onboarding.settings.muted_channels || []).join(", ")
              hasCursor: root.hasCursor(this); function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }
            TextField {
              id: alwaysChannelsField
              width: parent.width
              placeholderText: root.t("alwaysChannels")
              text: (onboarding.settings.always_channels || []).join(", ")
              hasCursor: root.hasCursor(this); function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }

            PanelSectionHeader { text: root.t("opening"); foreground: root.foreground; fontFamily: root.fontFamily }
            ComboBox {
              id: openModeField
              width: parent.width
              model: ["auto", "desktop", "browser"]
              currentIndex: Math.max(0, model.indexOf(String(onboarding.settings.open_mode || "auto")))
              property bool hasCursor: root.hasCursor(this)
              function keyboardActivate() { forceActiveFocus(); popup.open() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
            }
            Toggle {
              id: launchersToggle
              width: parent.width
              label: root.t("createLaunchers")
              checked: onboarding.settings.launchers !== false
              hasCursor: root.hasCursor(this)
              function keyboardActivate() { clicked() }
              onClicked: checked = !checked
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            TextField {
              id: desktopCommandField
              width: parent.width
              placeholderText: root.t("desktopCommand")
              text: String(onboarding.settings.desktop_command_text || "")
              hasCursor: root.hasCursor(this); function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }
            TextField {
              id: workspaceCommandField
              width: parent.width
              placeholderText: root.t("workspaceCommand")
              text: String(onboarding.settings.workspace_launch_command_text || "")
              hasCursor: root.hasCursor(this); function keyboardActivate() { forceActiveFocus() }
              onHoveredChanged: if (hovered) root.selectTarget(this)
              Keys.onPressed: function(event) { if (event.key === Qt.Key_Escape) { root.leaveEditor(); event.accepted = true } }
            }
            Button {
              id: saveSettingsButton
              text: onboarding.busy ? root.t("saving") : root.t("saveSettings")
              enabled: !onboarding.busy && groupWindowField.acceptableInput
              onClicked: onboarding.saveSettings({
                notifications_enabled: notifyEnabled.checked,
                private_messages: notifyPrivate.checked,
                mentions: notifyMentions.checked,
                followed_topics: notifyFollowed.checked,
                other_messages: notifyOther.checked,
                hide_content_when_locked: notifyLocked.checked,
                group_window_seconds: parseInt(groupWindowField.text, 10),
                muted_channels: root.splitChannels(mutedChannelsField.text),
                always_channels: root.splitChannels(alwaysChannelsField.text),
                open_mode: openModeField.currentText,
                launchers: launchersToggle.checked,
                desktop_command: desktopCommandField.text,
                workspace_launch_command: workspaceCommandField.text
              })
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }

            PanelSectionHeader { text: root.t("accounts"); foreground: root.foreground; fontFamily: root.fontFamily }
            Column {
              id: accountList
              width: parent.width
              spacing: Style.space(6)
              Repeater {
                model: onboarding.accounts
                Button {
                  required property var modelData
                  width: accountList.width
                  text: String(modelData.label || modelData.site) + " — " + String(modelData.email || "")
                    + (modelData.has_secret === true ? "" : "  (" + root.t("missingKey") + ")")
                  enabled: !onboarding.busy
                  onClicked: root.beginAccountEdit(modelData)
                  hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
                  onHovered: function(value) { if (value) root.selectTarget(this) }
                }
              }
            }
            Button {
              id: addAccountButton
              width: parent.width
              visible: onboarding.accounts.length < onboarding.maxAccounts
              text: root.t("addAccount")
              enabled: !onboarding.busy
              onClicked: root.beginAccountEdit(null)
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }

            PanelSectionHeader { text: root.t("accountService"); foreground: root.foreground; fontFamily: root.fontFamily }
            Button {
              id: diagnosticsButton
              width: parent.width
              text: root.t("openDiagnostics")
              enabled: !onboarding.busy
              onClicked: {
                onboarding.settingsOpen = false
                onboarding.diagnosticsOpen = true
                onboarding.loadDiagnostics()
              }
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            Button {
              id: serviceButton
              width: parent.width
              text: onboarding.active ? root.t("pauseService") : root.t("resumeService")
              enabled: !onboarding.busy
              onClicked: {
                if (onboarding.active) onboarding.deactivate()
                else onboarding.activate()
              }
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            PanelSectionHeader { text: root.t("osIntegration"); foreground: root.foreground; fontFamily: root.fontFamily }
            Text {
              width: parent.width
              text: root.t("osIntegrationDescription")
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Button {
              id: osIntegrationButton
              width: parent.width
              text: osProcess.running ? root.t("working")
                : (root.osIntegrationInstalled ? root.t("removeShortcuts") : root.t("enableShortcuts"))
              enabled: !osProcess.running
              onClicked: root.osIntegration(root.osIntegrationInstalled ? "remove" : "install")
              hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
              onHovered: function(value) { if (value) root.selectTarget(this) }
            }
            Text {
              visible: onboarding.error !== "" || onboarding.message !== ""
              width: parent.width
              text: onboarding.error !== "" ? onboarding.error : onboarding.message
              wrapMode: Text.WordWrap
              color: onboarding.error !== "" ? root.urgent : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          Column {
            visible: root.showDiagnostics
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader { text: root.t("diagnostics"); foreground: root.foreground; fontFamily: root.fontFamily }
            DiagnosticRow { label: root.t("service"); value: String(onboarding.diagnostics.service || onboarding.service) }
            DiagnosticRow { label: root.t("zulipApi"); value: onboarding.diagnostics.connected === true ? root.t("apiConnected") : root.t("apiOffline") }
            DiagnosticRow { label: root.t("lastSync"); value: String(onboarding.diagnostics.last_sync || root.t("absent")) }
            DiagnosticRow { label: root.t("localState"); value: onboarding.diagnostics.state_available === true ? root.t("available") : root.t("absent") }
            Repeater {
              model: (onboarding.diagnostics.accounts || []).length > 1 ? onboarding.diagnostics.accounts : []
              DiagnosticRow {
                required property var modelData
                label: String(modelData.name || modelData.site)
                value: modelData.connected === true ? root.t("apiConnected")
                  : (String(modelData.error || "") !== "" ? String(modelData.error) : root.t("apiOffline"))
              }
            }
            Text {
              visible: String(onboarding.diagnostics.bridge_error || "") !== ""
              width: parent.width
              text: String(onboarding.diagnostics.bridge_error || "")
              wrapMode: Text.WordWrap
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Column {
              width: parent.width
              spacing: Style.space(6)
              Button {
                id: diagnosticsBackButton
                width: parent.width
                text: root.t("backSettings")
                enabled: !onboarding.busy
                onClicked: {
                  onboarding.diagnosticsOpen = false
                  onboarding.settingsOpen = true
                }
                hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
                onHovered: function(value) { if (value) root.selectTarget(this) }
              }
              Button {
                id: diagnosticsRefreshButton
                width: parent.width
                text: root.t("refresh")
                enabled: !onboarding.busy
                onClicked: onboarding.loadDiagnostics()
                hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
                onHovered: function(value) { if (value) root.selectTarget(this) }
              }
              Button {
                id: reconnectButton
                width: parent.width
                text: root.t("reconnect")
                enabled: !onboarding.busy
                onClicked: onboarding.reconnect()
                hasCursor: root.hasCursor(this); focusable: true; function keyboardActivate() { clicked() }
                onHovered: function(value) { if (value) root.selectTarget(this) }
              }
            }
          }

          RowLayout {
            visible: hub.loaded && root.showHome
            width: parent.width
            spacing: Style.space(8)

            CountPill { label: root.t("total"); count: hub.unread.total }
            CountPill { label: root.t("mentions").toUpperCase(); count: hub.unread.mentions; emphasized: count > 0 }
            CountPill { label: root.t("direct"); count: hub.unread.private }
          }

          Column {
            visible: root.showHome && updates.restartNeeded
            width: parent.width
            spacing: Style.space(6)

            Text {
              width: parent.width
              text: root.t("restartNeeded").replace("%1", updates.installedVersion)
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              horizontalAlignment: Text.AlignHCenter
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Button {
              id: restartShellButton
              width: parent.width
              text: updates.restarting ? root.t("restarting") : root.t("restartNow")
              enabled: !updates.restarting
              onClicked: updates.restartShell()
              hasCursor: root.hasCursor(this)
              focusable: true
              function keyboardActivate() { clicked() }
              onHovered: function(isHovered) { if (isHovered) root.selectTarget(this) }
            }
          }

          Text {
            visible: root.showHome && (!hub.loaded || hub.lastError !== "")
            width: parent.width
            text: root.localizedStatus
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            color: hub.lastError !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            visible: root.actionError !== ""
            width: parent.width
            text: root.actionError
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }


          Text {
            visible: root.actionMessage !== "" && root.showHome
            width: parent.width
            text: root.actionMessage
            textFormat: Text.PlainText
            horizontalAlignment: Text.AlignHCenter
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          PanelSeparator {
            visible: hub.loaded && root.showHome
            foreground: root.foreground
          }

          Column {
            visible: hub.loaded && root.showHome
            width: parent.width
            spacing: Style.space(9)

            PanelSectionHeader {
              text: root.t("recentUnread")
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              visible: hub.recent.length === 0
              width: parent.width
              text: root.t("noRecent")
              textFormat: Text.PlainText
              horizontalAlignment: Text.AlignHCenter
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Column {
              id: messageColumn
              visible: hub.recent.length > 0
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: hub.recent
                MessageRow {
                  required property var modelData
                  required property int index
                  width: messageColumn.width
                  row: modelData
                  rowIndex: index
                }
              }
            }
          }

          Text {
            visible: hub.loaded && root.showHome
            width: parent.width
            text: root.t("homeHelp")
            textFormat: Text.PlainText
            // La liste des touches depasse la largeur du panneau : elle doit
            // passer a la ligne plutot que d etre coupee aux deux bouts.
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }

  Timer {
    interval: 60000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.nowMilliseconds = Date.now()
  }

  Process {
    id: openProcess
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") root.actionError = text.trim()
    }
  }

  Process {
    id: readProcess
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") root.actionError = text.trim()
    }
  }

  Process {
    id: workspaceProcess
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") root.actionError = text.trim()
    }
  }

  Process {
    id: osProcess
    stdout: SplitParser {
      onRead: function(value) { root.osResponseLine = String(value || "") }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") root.actionError = text.trim()
    }
    onExited: function(exitCode) {
      if (root.osResponseLine !== "") {
        try {
          var response = JSON.parse(root.osResponseLine)
          root.osIntegrationInstalled = response.installed === true
          if (exitCode === 0) root.actionMessage = root.osIntegrationInstalled
            ? root.t("shortcutsEnabled") : root.t("shortcutsDisabled")
        } catch (exception) {
          root.actionError = root.t("invalidLocal")
        }
      }
    }
  }

  component DiagnosticRow: RowLayout {
    property string label: ""
    property string value: ""
    width: parent ? parent.width : implicitWidth
    Text {
      text: label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
    }
    Item { Layout.fillWidth: true }
    Text {
      text: value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
    }
  }

  component CountPill: Rectangle {
    property string label: ""
    property int count: 0
    property bool emphasized: false

    Layout.fillWidth: true
    implicitHeight: countColumn.implicitHeight + Style.space(12)
    radius: Style.cornerRadius
    color: emphasized ? Style.selectedFillFor(root.foreground, Color.accent) : "transparent"
    border.width: 1
    border.color: emphasized ? root.urgent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.22)

    Column {
      id: countColumn
      anchors.centerIn: parent
      spacing: Style.space(1)
      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: String(count)
        color: emphasized ? root.urgent : root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.heading
        font.bold: true
      }
      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: label
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
      }
    }
  }

  component MessageRow: CursorSurface {
    id: messageRow
    required property var row
    required property int rowIndex

    hasCursor: root.hasCursor(messageRow)
    foreground: root.foreground
    implicitHeight: rowLayout.implicitHeight + Style.space(16)
    function keyboardActivate() { root.readMessage(row) }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.selectRow(messageRow.rowIndex)
      onClicked: root.readMessage(messageRow.row)
    }

    RowLayout {
      id: rowLayout
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(10)

      Rectangle {
        Layout.preferredWidth: Style.space(30)
        Layout.preferredHeight: Style.space(30)
        Layout.alignment: Qt.AlignVCenter
        radius: width / 2
        color: Model.isMention(messageRow.row)
          ? Style.selectedFillFor(root.foreground, Color.accent)
          : Style.hoverFillFor(root.foreground, Color.accent)
        Text {
          anchors.centerIn: parent
          text: String(messageRow.row.sender || "?").charAt(0).toUpperCase()
          color: Model.isMention(messageRow.row) ? root.urgent : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
        }
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        Text {
          Layout.fillWidth: true
          text: messageRow.row.sender
          textFormat: Text.PlainText
          color: Model.isMention(messageRow.row) ? root.urgent : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
          elide: Text.ElideRight
        }
        Text {
          Layout.fillWidth: true
          text: root.accountPrefix(messageRow.row) + (messageRow.row.type === "private" ? root.t("directConversation")
            : (messageRow.row.channel && messageRow.row.topic
              ? "#" + messageRow.row.channel + "  ›  " + messageRow.row.topic
              : (messageRow.row.channel ? "#" + messageRow.row.channel : root.t("channelMessage"))))
          textFormat: Text.PlainText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
        }
      }

      Text {
        text: Model.relativeTime(messageRow.row.timestamp, root.nowMilliseconds)
        textFormat: Text.PlainText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        Layout.alignment: Qt.AlignTop
      }

      PanelActionButton {
        iconText: "↗"
        tooltipText: root.t("openInZulip")
        foreground: root.foreground
        hoverColor: root.foreground
        fontFamily: root.fontFamily
        enabled: !openProcess.running
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.openInZulip(messageRow.row)
      }

      PanelActionButton {
        iconText: "↩"
        tooltipText: root.t("reply")
        foreground: root.foreground
        hoverColor: root.foreground
        fontFamily: root.fontFamily
        enabled: composer.available === true && !composer.busy
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.replyToMessage(messageRow.row)
      }

      PanelActionButton {
        iconText: "✓"
        tooltipText: root.t("markRead")
        foreground: root.foreground
        hoverColor: root.foreground
        fontFamily: root.fontFamily
        enabled: !readProcess.running
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.markMessageRead(messageRow.row)
      }
    }
  }
}
