import { useEffect, useRef, useState } from 'react'

import VisionControl from './VisionControl'

const DEFAULT_PRESETS = ['HOME', 'LIFT', 'CYCLE', 'OPEN', 'CLOSE', 'WAVE', 'DEMO', 'PARK', 'LEFT', 'CENTER', 'RIGHT']
const VIEW_TABS = [
  ['chat', 'Chat'],
  ['vision', 'Vision'],
  ['hardware', 'Hardware'],
  ['control', 'Control'],
  ['agent', 'Agent'],
]
const SUGGESTED_PROMPTS = [
  'Покажи текущий статус руки.',
  'Поверни base на 10 градусов вправо.',
  'Прими исходное положение.',
]
const EMPTY_RUNTIME_CONFIG = {
  base_url: 'http://127.0.0.1:1234/v1',
  model: '',
  temperature: 0.2,
  tools_enabled: true,
  system_prompt: '',
}
const VOICE_LANGUAGE = 'ru-RU'

function Panel({ title, actions, children, className = '' }) {
  return (
    <section className={`panel ${className}`.trim()}>
      <div className="panel-header">
        <h2>{title}</h2>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </div>
      <div className="panel-body">{children}</div>
    </section>
  )
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Number(value)))
}

function buildInitialSliders(jointLimits, robotState) {
  const next = {}
  Object.entries(jointLimits || {}).forEach(([joint, limits]) => {
    const fallback = limits.default_angle ?? 0
    next[joint] = robotState?.joints?.[joint] ?? fallback
  })
  return next
}

function mergeSliderValues(jointLimits, robotState, prev, dirtyMap) {
  const next = { ...prev }
  Object.entries(jointLimits || {}).forEach(([joint, limits]) => {
    const fallback = robotState?.joints?.[joint] ?? limits.default_angle ?? 0
    if (next[joint] === undefined || !dirtyMap[joint]) {
      next[joint] = fallback
    }
  })
  return next
}

function pickPreferredPort(ports, current) {
  if (current && ports.some((port) => port.device === current)) return current
  const arduinoPort = ports.find((port) => /arduino/i.test(port.description || ''))
  return arduinoPort?.device || ports[0]?.device || ''
}

function normalizeToolName(tool) {
  return tool?.function?.name || tool?.name || 'tool'
}

function getMessageBody(message) {
  if (message.role === 'assistant' && !message.content && message.tool_calls?.length) {
    return 'Calling safe tools...'
  }
  if (message.role === 'tool' && !message.content) {
    return 'Tool returned no content.'
  }
  return message.content || ''
}

function tryParseToolOutput(content) {
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

function getMessageTitle(message) {
  if (message.role === 'user') return 'You'
  if (message.role === 'tool') return message.name || 'Tool'
  return 'AIO'
}

function StatusChip({ label, value, tone = 'neutral' }) {
  return (
    <div className={`status-chip ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function SessionItem({ item, active, onSelect }) {
  return (
    <button className={active ? 'session-item active' : 'session-item'} onClick={() => onSelect(item.id)}>
      <strong>{item.title}</strong>
      <span>{item.message_count} msgs</span>
    </button>
  )
}

function MessageRow({ message }) {
  const toolOutput = message.role === 'tool' ? tryParseToolOutput(message.content) : null
  const body = getMessageBody(message)
  const toolCalls = message.tool_calls || []

  return (
    <article className={`message-row ${message.role}`}>
      <div className="message-header">
        <span className="message-role">{getMessageTitle(message)}</span>
        <span className="message-time">{formatTime(message.created_at)}</span>
      </div>

      {body ? <div className="message-content">{body}</div> : null}

      {toolCalls.length ? (
        <div className="tool-chip-row">
          {toolCalls.map((toolCall) => (
            <span key={toolCall.id} className="tool-chip">
              {normalizeToolName(toolCall)}
            </span>
          ))}
        </div>
      ) : null}

      {message.role === 'tool' ? (
        <div className="tool-output">
          <pre>{JSON.stringify(toolOutput || message.content, null, 2)}</pre>
        </div>
      ) : null}
    </article>
  )
}

function ToolSpecList({ tools }) {
  return (
    <div className="tool-spec-list">
      {tools.map((tool) => (
        <div key={tool.function.name} className="tool-spec-card">
          <strong>{tool.function.name}</strong>
          <span>{tool.function.description}</span>
        </div>
      ))}
    </div>
  )
}

function modelIdentifierFromRuntime(model) {
  return model?.identifier || model?.modelKey || model?.id || ''
}

function App() {
  const [error, setError] = useState('')
  const [robotState, setRobotState] = useState(null)
  const [logs, setLogs] = useState([])
  const [jointLimits, setJointLimits] = useState({})
  const [supportedPresets, setSupportedPresets] = useState(DEFAULT_PRESETS)
  const [sliderValues, setSliderValues] = useState({})
  const [sliderDirty, setSliderDirty] = useState({})
  const [ports, setPorts] = useState([])
  const [selectedPort, setSelectedPort] = useState('')
  const [baudRate, setBaudRate] = useState(115200)
  const [manualBusy, setManualBusy] = useState(false)
  const [visionControlActive, setVisionControlActive] = useState(false)
  const [sessions, setSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState('')
  const [activeSession, setActiveSession] = useState(null)
  const [composerText, setComposerText] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [activeTab, setActiveTab] = useState('chat')
  const [runtimeConfig, setRuntimeConfig] = useState(EMPTY_RUNTIME_CONFIG)
  const [runtimeDraft, setRuntimeDraft] = useState(EMPTY_RUNTIME_CONFIG)
  const [chatTools, setChatTools] = useState([])
  const [availableModels, setAvailableModels] = useState([])
  const [modelsBusy, setModelsBusy] = useState(false)
  const [runtimeInfo, setRuntimeInfo] = useState({ server_running: false, local_models: [], loaded_models: [] })
  const [runtimeBusy, setRuntimeBusy] = useState(false)
  const [cameraCommand, setCameraCommand] = useState(null)
  const [speechRecognitionSupported, setSpeechRecognitionSupported] = useState(false)
  const [speechSynthesisSupported, setSpeechSynthesisSupported] = useState(false)
  const [listening, setListening] = useState(false)
  const [ttsEnabled, setTtsEnabled] = useState(true)

  const sliderDirtyRef = useRef({})
  const messagesRef = useRef(null)
  const recognitionRef = useRef(null)
  const spokenMessageRef = useRef('')
  const handledUiToolMessageRef = useRef('')

  function updateDirtyState(updater) {
    setSliderDirty((prev) => {
      const next = typeof updater === 'function' ? updater(prev) : updater
      sliderDirtyRef.current = next
      return next
    })
  }

  function clearAllDirty() {
    updateDirtyState({})
  }

  function clearDirtyJoints(jointNames) {
    updateDirtyState((prev) => {
      const next = { ...prev }
      jointNames.forEach((joint) => {
        delete next[joint]
      })
      return next
    })
  }

  function markJointDirty(jointName) {
    updateDirtyState((prev) => ({ ...prev, [jointName]: true }))
  }

  async function fetchStatus() {
    const response = await fetch('/api/status')
    if (!response.ok) throw new Error('Failed to fetch robot status')
    const data = await response.json()
    setRobotState(data.robot_state)
    setLogs(data.logs || [])
    setJointLimits(data.joint_limits || {})
    setSupportedPresets(data.supported_presets?.length ? data.supported_presets : DEFAULT_PRESETS)
    setSliderValues((prev) => mergeSliderValues(data.joint_limits, data.robot_state, prev, sliderDirtyRef.current))
    return data
  }

  async function fetchPorts() {
    const response = await fetch('/api/hardware/ports')
    if (!response.ok) throw new Error('Failed to fetch serial ports')
    const data = await response.json()
    const portList = data.ports || []
    setPorts(portList)
    setSelectedPort((current) => pickPreferredPort(portList, current))
    return portList
  }

  async function fetchChatRuntime() {
    const response = await fetch('/api/chat/config')
    if (!response.ok) throw new Error('Failed to fetch chat runtime')
    const data = await response.json()
    setRuntimeConfig(data.config)
    setRuntimeDraft(data.config)
    setChatTools(data.tools || [])
    return data
  }

  async function fetchLmRuntime() {
    const response = await fetch('/api/chat/runtime')
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to fetch LM Studio runtime')
    }
    setRuntimeInfo({
      server_running: Boolean(data.server_running),
      local_models: data.local_models || [],
      loaded_models: data.loaded_models || [],
    })
    return data
  }

  async function fetchSessions() {
    const response = await fetch('/api/chat/sessions')
    if (!response.ok) throw new Error('Failed to fetch chat sessions')
    const data = await response.json()
    setSessions(data.sessions || [])
    return data.sessions || []
  }

  async function fetchSession(sessionId) {
    const response = await fetch(`/api/chat/sessions/${sessionId}`)
    if (!response.ok) throw new Error('Failed to fetch selected chat')
    const data = await response.json()
    setActiveSession(data.session)
    return data.session
  }

  async function createSession(title = 'New chat') {
    const response = await fetch('/api/chat/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    })
    if (!response.ok) throw new Error('Failed to create chat')
    const data = await response.json()
    setActiveSessionId(data.session.id)
    setActiveSession(data.session)
    setActiveTab('chat')
    await fetchSessions()
    return data.session
  }

  async function bootstrap() {
    try {
      setError('')
      await Promise.all([fetchStatus(), fetchPorts(), fetchChatRuntime(), fetchLmRuntime()])
      const loadedSessions = await fetchSessions()

      if (!loadedSessions.length) {
        await createSession('AIO')
        return
      }

      const initialSessionId = loadedSessions[0].id
      setActiveSessionId(initialSessionId)
      await fetchSession(initialSessionId)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    void bootstrap()
  }, [])

  useEffect(() => {
    const interval = window.setInterval(() => {
      void fetchStatus().catch((err) => setError(err.message))
    }, 1500)
    return () => window.clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!activeSessionId) return
    void fetchSession(activeSessionId).catch((err) => setError(err.message))
  }, [activeSessionId])

  useEffect(() => {
    if (typeof window === 'undefined') return undefined

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    setSpeechRecognitionSupported(Boolean(SpeechRecognition))
    setSpeechSynthesisSupported(Boolean(window.speechSynthesis))

    if (!SpeechRecognition) return undefined

    const recognition = new SpeechRecognition()
    recognition.lang = VOICE_LANGUAGE
    recognition.continuous = false
    recognition.interimResults = false
    recognition.maxAlternatives = 1
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .map((result) => result[0]?.transcript || '')
        .join(' ')
        .trim()

      if (!transcript) return
      setComposerText((prev) => (prev.trim() ? `${prev.trim()} ${transcript}` : transcript))
    }
    recognition.onend = () => {
      setListening(false)
    }
    recognition.onerror = (event) => {
      setListening(false)
      if (event.error !== 'aborted' && event.error !== 'no-speech') {
        setError(`STT error: ${event.error}`)
      }
    }

    recognitionRef.current = recognition

    return () => {
      recognition.onresult = null
      recognition.onend = null
      recognition.onerror = null
      try {
        recognition.stop()
      } catch {
        // no-op
      }
      recognitionRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!messagesRef.current) return
    messagesRef.current.scrollTop = messagesRef.current.scrollHeight
  }, [activeSession])

  useEffect(() => {
    const latestToolMessage = [...(activeSession?.messages || [])]
      .reverse()
      .find((message) => message.role === 'tool' && message.content)

    if (!latestToolMessage || handledUiToolMessageRef.current === latestToolMessage.id) return

    const payload = tryParseToolOutput(latestToolMessage.content)
    const uiAction = payload?.ui_action
    if (!uiAction) return

    handledUiToolMessageRef.current = latestToolMessage.id

    if (uiAction === 'open_camera') {
      setActiveTab('vision')
      setCameraCommand({ action: 'open', token: Date.now() })
      return
    }

    if (uiAction === 'close_camera') {
      setCameraCommand({ action: 'close', token: Date.now() })
    }
  }, [activeSession])

  useEffect(() => {
    const latestAssistantMessage = [...(activeSession?.messages || [])]
      .reverse()
      .find((message) => message.role === 'assistant' && message.content && !message.tool_calls?.length)

    spokenMessageRef.current = latestAssistantMessage?.id || ''
  }, [activeSession?.id])

  useEffect(() => {
    if (!ttsEnabled || !speechSynthesisSupported || activeTab !== 'chat') return
    if (typeof window === 'undefined' || !window.speechSynthesis) return

    const latestAssistantMessage = [...(activeSession?.messages || [])]
      .reverse()
      .find((message) => message.role === 'assistant' && message.content && !message.tool_calls?.length)

    if (!latestAssistantMessage) return
    if (spokenMessageRef.current === latestAssistantMessage.id) return

    spokenMessageRef.current = latestAssistantMessage.id
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(latestAssistantMessage.content)
    utterance.lang = VOICE_LANGUAGE
    utterance.rate = 1
    window.speechSynthesis.speak(utterance)
  }, [activeSession, activeTab, speechSynthesisSupported, ttsEnabled])

  useEffect(() => {
    return () => {
      if (typeof window !== 'undefined' && window.speechSynthesis) {
        window.speechSynthesis.cancel()
      }
    }
  }, [])

  async function callManualApi(path, payload, dirtyReset = null) {
    setManualBusy(true)
    setError('')
    try {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload ? JSON.stringify(payload) : undefined,
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(data.detail || 'Manual action failed')
      }

      setRobotState(data.robot_state)
      setLogs(data.logs || [])

      if (dirtyReset === 'all') {
        clearAllDirty()
      } else if (Array.isArray(dirtyReset)) {
        clearDirtyJoints(dirtyReset)
      }

      await fetchStatus()
      return data
    } catch (err) {
      setError(err.message)
      throw err
    } finally {
      setManualBusy(false)
    }
  }

  async function handleConnectHardware() {
    if (!selectedPort) {
      setError('Select a serial port first')
      return
    }

    await callManualApi('/api/hardware/connect', { port: selectedPort, baud_rate: Number(baudRate) })
  }

  async function handleDisconnectHardware() {
    await callManualApi('/api/hardware/disconnect', {}, 'all')
  }

  async function handleSendJoint(jointName) {
    const angle = Number(sliderValues[jointName])
    await callManualApi('/api/manual/joint', { joint_name: jointName, angle }, [jointName])
  }

  async function handleApplyPose() {
    const joints = {}
    Object.entries(sliderValues).forEach(([joint, value]) => {
      joints[joint] = Number(value)
    })
    await callManualApi('/api/manual/pose', { joints }, 'all')
  }

  async function handleRunPreset(preset) {
    await callManualApi(`/api/manual/preset/${preset}`, {}, 'all')
  }

  async function handleStop() {
    await callManualApi('/api/manual/stop', {}, 'all')
  }

  async function submitVisionPose(joints) {
    const response = await fetch('/api/manual/pose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ joints }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      const message = data.detail || 'Vision pose failed'
      setError(message)
      throw new Error(message)
    }

    setRobotState(data.robot_state)
    setLogs(data.logs || [])
    return data
  }

  async function handleSendMessage(content) {
    const text = content.trim()
    if (!text) return

    setChatBusy(true)
    setError('')
    try {
      let sessionId = activeSessionId
      if (!sessionId) {
        const session = await createSession('AIO')
        sessionId = session.id
      }

      const response = await fetch(`/api/chat/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(data.detail || 'Chat request failed')
      }

      setActiveSession(data.session)
      setActiveSessionId(data.session.id)
      setActiveTab('chat')
      setComposerText('')
      await Promise.all([fetchSessions(), fetchStatus()])
    } catch (err) {
      setError(err.message)
    } finally {
      setChatBusy(false)
    }
  }

  async function handleSaveRuntime() {
    setError('')
    try {
      await persistRuntimeDraft(runtimeDraft)
    } catch (err) {
      setError(err.message)
    }
  }

  async function persistRuntimeDraft(nextDraft = runtimeDraft) {
    const response = await fetch('/api/chat/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(nextDraft),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to save runtime config')
    }
    setRuntimeConfig(data.config)
    setRuntimeDraft(data.config)
    setChatTools(data.tools || [])
    return data
  }

  async function handleFetchModels() {
    setModelsBusy(true)
    setError('')
    try {
      const response = await fetch('/api/chat/models')
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to fetch models from LM Studio')
      }
      setAvailableModels(data.models || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setModelsBusy(false)
    }
  }

  async function handleStartRuntime() {
    setRuntimeBusy(true)
    setError('')
    try {
      const response = await fetch('/api/chat/runtime/start', { method: 'POST' })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to start LM Studio server')
      }
      setRuntimeInfo({
        server_running: Boolean(data.server_running),
        local_models: data.local_models || [],
        loaded_models: data.loaded_models || [],
      })
      await fetchChatRuntime()
      await handleFetchModels()
    } catch (err) {
      setError(err.message)
    } finally {
      setRuntimeBusy(false)
    }
  }

  async function handleRefreshRuntime() {
    setRuntimeBusy(true)
    setError('')
    try {
      await fetchLmRuntime()
      await fetchChatRuntime()
      await handleFetchModels()
    } catch (err) {
      setError(err.message)
    } finally {
      setRuntimeBusy(false)
    }
  }

  async function handleLoadRuntimeModel(modelId = runtimeDraft.model) {
    const model = modelId.trim()
    if (!model) {
      setError('Choose a model first')
      return
    }

    setRuntimeBusy(true)
    setError('')
    let configSaved = false
    try {
      await persistRuntimeDraft({ ...runtimeDraft, model })
      configSaved = true
      const response = await fetch('/api/chat/runtime/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(data.detail || 'Model id was saved, but LM Studio CLI could not load the model')
      }
      setRuntimeInfo({
        server_running: Boolean(data.server_running),
        local_models: data.local_models || [],
        loaded_models: data.loaded_models || [],
      })
      await fetchChatRuntime()
      setRuntimeDraft((prev) => ({ ...prev, model }))
      await handleFetchModels()
    } catch (err) {
      if (configSaved) {
        setError(`${err.message}. The model id was still saved for chat use.`)
      } else {
        setError(err.message)
      }
    } finally {
      setRuntimeBusy(false)
    }
  }

  function handleToggleListening() {
    if (!speechRecognitionSupported || !recognitionRef.current) {
      setError('STT is not available in this browser')
      return
    }

    if (listening) {
      recognitionRef.current.stop()
      return
    }

    setError('')
    try {
      recognitionRef.current.start()
      setListening(true)
    } catch (err) {
      setListening(false)
      setError(err.message || 'Failed to start microphone input')
    }
  }

  function handleToggleTts() {
    const nextEnabled = !ttsEnabled
    setTtsEnabled(nextEnabled)

    if (!nextEnabled && typeof window !== 'undefined' && window.speechSynthesis) {
      window.speechSynthesis.cancel()
    }
  }

  const hardwareReady = Boolean(robotState?.hardware_connected)
  const visionLocked = visionControlActive
  const activeMessages = activeSession?.messages || []
  const loadedModelId = runtimeInfo.loaded_models?.[0] ? modelIdentifierFromRuntime(runtimeInfo.loaded_models[0]) : ''
  const activeModelId = loadedModelId || runtimeDraft.model || runtimeConfig.model || ''

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="sidebar-title">AIO</div>
          <div className="sidebar-subtitle">Local robot control console</div>
        </div>

        <button className="create-chat-button" onClick={() => void createSession('New chat')}>
          New chat
        </button>

        <div className="sidebar-section-label">Chats</div>
        <div className="session-list">
          {sessions.map((item) => (
            <SessionItem key={item.id} item={item} active={item.id === activeSessionId} onSelect={setActiveSessionId} />
          ))}
        </div>
      </aside>

      <main className="workspace">
        <div className="main-column">
          <header className="workspace-header">
            <div className="workspace-heading">
              <h1>{activeSession?.title || 'AIO'}</h1>
              <p>Local chat, vision, and robot control.</p>
            </div>

            <div className="workspace-status">
              <StatusChip label="Robot" value={hardwareReady ? 'online' : 'offline'} tone={hardwareReady ? 'good' : 'neutral'} />
              <StatusChip label="LM Studio" value={runtimeInfo.server_running ? 'running' : 'stopped'} tone={runtimeInfo.server_running ? 'good' : 'neutral'} />
              <StatusChip label="Model" value={activeModelId || 'not set'} tone={activeModelId ? 'accent' : 'neutral'} />
            </div>
          </header>

          <div className="view-tabs">
            {VIEW_TABS.map(([key, label]) => (
              <button key={key} className={activeTab === key ? 'view-tab active' : 'view-tab'} onClick={() => setActiveTab(key)}>
                {label}
              </button>
            ))}
          </div>

          {error ? <div className="error-banner">{error}</div> : null}

          <div className="content-area">
            {activeTab === 'chat' ? (
              <section className="chat-view">
                <div className="chat-transcript" ref={messagesRef}>
                  {activeMessages.length ? (
                    activeMessages.map((message) => <MessageRow key={message.id} message={message} />)
                  ) : (
                    <div className="empty-chat">
                      <strong>Ask for one small thing.</strong>
                      <p>Check status, move one joint a little, or return to a safe pose.</p>
                      <div className="suggestion-row">
                        {SUGGESTED_PROMPTS.map((prompt) => (
                          <button key={prompt} className="suggestion-button" onClick={() => void handleSendMessage(prompt)} disabled={chatBusy}>
                            {prompt}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="composer">
                  <div className="voice-toolbar">
                    <div className="voice-controls">
                      <button
                        className={listening ? 'secondary-button voice-button active' : 'secondary-button voice-button'}
                        onClick={handleToggleListening}
                        disabled={!speechRecognitionSupported || chatBusy}
                      >
                        {listening ? 'Stop mic' : 'Mic'}
                      </button>
                      <button
                        className={ttsEnabled ? 'secondary-button voice-button active' : 'secondary-button voice-button'}
                        onClick={handleToggleTts}
                        disabled={!speechSynthesisSupported}
                      >
                        {ttsEnabled ? 'Voice on' : 'Voice off'}
                      </button>
                    </div>
                    <span className="voice-hint">
                      {listening
                        ? 'Listening…'
                        : speechRecognitionSupported
                          ? 'Mic ready'
                          : 'Mic unavailable in this browser'}
                    </span>
                  </div>

                  <textarea
                    value={composerText}
                    onChange={(event) => setComposerText(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault()
                        void handleSendMessage(composerText)
                      }
                    }}
                    placeholder="Write a message..."
                    rows={4}
                    disabled={chatBusy}
                  />

                  <div className="composer-actions">
                    <span>Shift+Enter for a new line.</span>
                    <button onClick={() => void handleSendMessage(composerText)} disabled={chatBusy || !composerText.trim()}>
                      {chatBusy ? 'Thinking…' : 'Send'}
                    </button>
                  </div>
                </div>
              </section>
            ) : null}

          {activeTab === 'vision' ? (
            <VisionControl
              robotState={robotState}
              jointLimits={jointLimits}
              onSendPose={submitVisionPose}
              onControlStateChange={setVisionControlActive}
              cameraCommand={cameraCommand}
              onTrace={(text) => {
                if (!activeSession) return
                setActiveSession((prev) => {
                  if (!prev) return prev
                  return {
                    ...prev,
                    messages: [
                      ...prev.messages,
                      {
                        id: `vision-${Date.now()}`,
                        role: 'assistant',
                        content: text,
                        created_at: new Date().toISOString(),
                        tool_calls: [],
                      },
                    ],
                  }
                })
              }}
            />
          ) : null}

          {activeTab === 'hardware' ? (
            <div className="stack">
              <Panel title="Connection">
                <div className="field-grid">
                  <label>
                    <span>Serial port</span>
                    <select value={selectedPort} onChange={(event) => setSelectedPort(event.target.value)}>
                      <option value="">Select port...</option>
                      {ports.map((port) => (
                        <option key={port.device} value={port.device}>
                          {port.device} - {port.description}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label>
                    <span>Baud rate</span>
                    <input type="number" value={baudRate} onChange={(event) => setBaudRate(event.target.value)} />
                  </label>
                </div>

                <div className="button-row">
                  <button className="secondary-button" onClick={() => void fetchPorts()}>
                    Refresh ports
                  </button>
                  <button onClick={() => void handleConnectHardware()} disabled={manualBusy}>
                    Connect
                  </button>
                  <button className="danger-button" onClick={() => void handleDisconnectHardware()} disabled={manualBusy || !hardwareReady}>
                    Disconnect
                  </button>
                </div>
              </Panel>

              <Panel title="Robot State">
                <div className="status-grid">
                  <StatusChip label="Hardware" value={hardwareReady ? 'connected' : 'offline'} tone={hardwareReady ? 'good' : 'neutral'} />
                  <StatusChip label="Pose" value={robotState?.active_pose || '-'} tone="neutral" />
                  <StatusChip label="Serial" value={robotState?.last_serial_message || '-'} tone="neutral" />
                </div>

                <div className="joint-grid">
                  {Object.entries(robotState?.joints || {}).map(([joint, value]) => (
                    <div key={joint} className="joint-card">
                      <span>{joint}</span>
                      <strong>{value} deg</strong>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel title="Runtime Log">
                <div className="log-list">
                  {logs.map((log, index) => (
                    <div key={`${log.timestamp}-${index}`} className={`log-item ${log.level}`}>
                      <div className="log-topline">
                        <span>{new Date(log.timestamp).toLocaleTimeString()}</span>
                        <strong>{log.source}</strong>
                        <span>{log.level}</span>
                      </div>
                      <div className="log-message">{log.message}</div>
                    </div>
                  ))}
                </div>
              </Panel>
            </div>
          ) : null}

          {activeTab === 'control' ? (
            <div className="stack">
              <Panel title="Manual Joint Control">
                {visionLocked ? (
                  <div className="note-card">Vision is actively steering base and shoulder. Manual sends for those joints are paused.</div>
                ) : null}

                {!hardwareReady ? (
                  <div className="note-card">Connect Arduino first. Direct movement stays disabled while hardware is offline.</div>
                ) : null}

                <div className="control-stack">
                  {Object.entries(jointLimits).map(([joint, limits]) => (
                    <div key={joint} className="servo-block">
                      <div className="servo-topline">
                        <strong>{joint}</strong>
                        <span>{sliderValues[joint] ?? limits.default_angle} deg</span>
                      </div>

                      <input
                        type="range"
                        min={limits.min_angle}
                        max={limits.max_angle}
                        step="1"
                        value={sliderValues[joint] ?? limits.default_angle}
                        onChange={(event) => {
                          const value = clamp(event.target.value, limits.min_angle, limits.max_angle)
                          setSliderValues((prev) => ({ ...prev, [joint]: value }))
                          markJointDirty(joint)
                        }}
                      />

                      <div className="servo-actions">
                        <span>{limits.min_angle} to {limits.max_angle} deg</span>
                        <span>robot: {robotState?.joints?.[joint] ?? limits.default_angle} deg</span>
                        <button
                          className="tiny-button"
                          onClick={() => void handleSendJoint(joint)}
                          disabled={manualBusy || !hardwareReady || (visionLocked && (joint === 'base' || joint === 'shoulder'))}
                        >
                          Send
                        </button>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="button-row">
                  <button onClick={() => void handleApplyPose()} disabled={manualBusy || !hardwareReady || visionLocked}>
                    Apply full pose
                  </button>
                  <button
                    className="secondary-button"
                    onClick={() => {
                      setSliderValues(buildInitialSliders(jointLimits, robotState))
                      clearAllDirty()
                    }}
                  >
                    Reset sliders
                  </button>
                  <button className="danger-button" onClick={() => void handleStop()} disabled={manualBusy || !hardwareReady}>
                    Stop
                  </button>
                </div>
              </Panel>

              <Panel title="Presets">
                <div className="note-card">
                  These presets now use backend-calibrated logical poses. Base is centered at 0, with negative angles to one side and positive
                  angles to the other.
                </div>

                <div className="preset-grid">
                  {supportedPresets.map((preset) => (
                    <button
                      key={preset}
                      className="preset-card"
                      onClick={() => void handleRunPreset(preset)}
                      disabled={manualBusy || !hardwareReady}
                    >
                      <strong>{preset}</strong>
                    </button>
                  ))}
                </div>
              </Panel>
            </div>
          ) : null}

            {activeTab === 'agent' ? (
              <div className="stack">
                <Panel
                  title="LM Studio"
                  actions={
                    <div className="button-row">
                      <button className="secondary-button" onClick={() => void handleRefreshRuntime()} disabled={runtimeBusy}>
                        Refresh runtime
                      </button>
                      <button className="secondary-button" onClick={() => void handleStartRuntime()} disabled={runtimeBusy}>
                        {runtimeBusy ? 'Working…' : 'Start server'}
                      </button>
                    </div>
                  }
                >
                  <div className="status-grid">
                    <StatusChip label="Server" value={runtimeInfo.server_running ? 'running' : 'stopped'} tone={runtimeInfo.server_running ? 'good' : 'neutral'} />
                    <StatusChip label="Loaded model" value={loadedModelId || 'none'} tone={loadedModelId ? 'accent' : 'neutral'} />
                  </div>

                  <div className="field-stack">
                    <label>
                      <span>Model identifier</span>
                      <input
                        value={runtimeDraft.model}
                        onChange={(event) => setRuntimeDraft((prev) => ({ ...prev, model: event.target.value }))}
                        placeholder="google/gemma-4-e4b"
                      />
                    </label>

                    <div className="runtime-hint">Paste a model id or click any model below to load it.</div>

                    <div className="button-row">
                      <button onClick={() => void handleLoadRuntimeModel()} disabled={runtimeBusy || !runtimeDraft.model.trim()}>
                        {runtimeBusy ? 'Working…' : 'Load model'}
                      </button>
                      <button className="secondary-button" onClick={() => void handleFetchModels()} disabled={modelsBusy}>
                        {modelsBusy ? 'Loading…' : 'Fetch API models'}
                      </button>
                    </div>

                    {runtimeInfo.local_models?.length ? (
                      <div className="field-stack">
                        <label>
                          <span>Local models</span>
                        </label>
                        <div className="model-list">
                          {runtimeInfo.local_models
                            .filter((model) => model.type === 'llm')
                            .map((model) => {
                              const modelId = modelIdentifierFromRuntime(model)
                              return (
                                <button
                                  key={modelId}
                                  className={activeModelId === modelId ? 'model-pill active' : 'model-pill'}
                                  onClick={() => void handleLoadRuntimeModel(modelId)}
                                  disabled={runtimeBusy}
                                >
                                  {modelId}
                                </button>
                              )
                            })}
                        </div>
                      </div>
                    ) : null}

                    {availableModels.length ? (
                      <div className="field-stack">
                        <label>
                          <span>Models exposed by the API</span>
                        </label>
                        <div className="model-list">
                          {availableModels.map((model) => (
                            <button
                              key={model.id}
                              className={activeModelId === model.id ? 'model-pill active' : 'model-pill'}
                              onClick={() => void handleLoadRuntimeModel(model.id)}
                              disabled={runtimeBusy}
                            >
                              {model.id}
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </Panel>

                <Panel title="Chat Runtime">
                  <div className="field-grid">
                    <label>
                      <span>Base URL</span>
                      <input
                        value={runtimeDraft.base_url}
                        onChange={(event) => setRuntimeDraft((prev) => ({ ...prev, base_url: event.target.value }))}
                        placeholder="http://127.0.0.1:1234/v1"
                      />
                    </label>

                    <label>
                      <span>Temperature</span>
                      <input
                        type="number"
                        min="0"
                        max="1.5"
                        step="0.1"
                        value={runtimeDraft.temperature}
                        onChange={(event) => setRuntimeDraft((prev) => ({ ...prev, temperature: Number(event.target.value) }))}
                      />
                    </label>
                  </div>

                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={runtimeDraft.tools_enabled}
                      onChange={(event) => setRuntimeDraft((prev) => ({ ...prev, tools_enabled: event.target.checked }))}
                    />
                    <span>Enable safe tool calling</span>
                  </label>

                  <div className="button-row">
                    <button onClick={() => void handleSaveRuntime()}>Save runtime</button>
                  </div>
                </Panel>

                <Panel title="Safe Tools">
                  <ToolSpecList tools={chatTools} />
                </Panel>
              </div>
            ) : null}
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
