import { useEffect, useMemo, useRef, useState } from 'react'

const MODE_OPTIONS = ['demo', 'sim', 'hardware']

const PRESET_COMMANDS = [
  'Покажи статус',
  'Перейди в домашнюю позицию',
  'Открой захват',
  'Подними руку',
]

const HARDWARE_PRESETS = [
  ['HOME', 'Home'],
  ['LIFT', 'Lift'],
  ['CYCLE', 'Cycle'],
  ['OPEN', 'Open'],
  ['CLOSE', 'Close'],
  ['WAVE', 'Wave'],
  ['DEMO', 'Demo'],
  ['PARK', 'Park'],
  ['LEFT', 'Left'],
  ['CENTER', 'Center'],
  ['RIGHT', 'Right'],
]

function Panel({ title, children, className = '' }) {
  return (
    <section className={`panel ${className}`.trim()}>
      <div className="panel-header">
        <h2>{title}</h2>
      </div>
      <div className="panel-body">{children}</div>
    </section>
  )
}

function Badge({ label, value, tone = 'default' }) {
  return (
    <div className={`badge-card ${tone}`}>
      <div className="badge-label">{label}</div>
      <div className="badge-value">{String(value)}</div>
    </div>
  )
}

function formatTs(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleTimeString()
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

function App() {
  const [messages, setMessages] = useState([
    {
      id: crypto.randomUUID(),
      role: 'assistant',
      text: 'Консоль готова. Можно подключать Arduino и управлять рукой вручную или командами.',
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [robotState, setRobotState] = useState(null)
  const [logs, setLogs] = useState([])
  const [jointLimits, setJointLimits] = useState({})
  const [currentMode, setCurrentMode] = useState('demo')
  const [sliderValues, setSliderValues] = useState({})
  const [sliderDirty, setSliderDirty] = useState({})
  const [ports, setPorts] = useState([])
  const [selectedPort, setSelectedPort] = useState('')
  const [baudRate, setBaudRate] = useState(115200)
  const [manualBusy, setManualBusy] = useState(false)

  const sliderDirtyRef = useRef({})

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
    try {
      const response = await fetch('/api/status')
      if (!response.ok) throw new Error('Failed to fetch status')
      const data = await response.json()
      setRobotState(data.robot_state)
      setLogs(data.logs)
      setJointLimits(data.joint_limits)
      setCurrentMode(data.robot_state.mode)
      setSliderValues((prev) =>
        mergeSliderValues(data.joint_limits, data.robot_state, prev, sliderDirtyRef.current)
      )
    } catch (err) {
      setError(err.message)
    }
  }

  async function fetchPorts() {
    try {
      const response = await fetch('/api/hardware/ports')
      if (!response.ok) throw new Error('Failed to fetch serial ports')
      const data = await response.json()
      setPorts(data.ports || [])
      if (!selectedPort && data.ports?.length) {
        setSelectedPort(data.ports[0].device)
      }
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    fetchStatus()
    fetchPorts()
  }, [])

  useEffect(() => {
    const interval = window.setInterval(() => {
      fetchStatus()
    }, 1500)
    return () => window.clearInterval(interval)
  }, [])

  async function handleModeChange(mode) {
    setCurrentMode(mode)
    setError('')
    try {
      const response = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })
      if (!response.ok) throw new Error('Failed to switch mode')
      const data = await response.json()
      setRobotState(data.robot_state)
      await fetchStatus()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleSend(messageOverride) {
    const text = (messageOverride ?? input).trim()
    if (!text) return
    setLoading(true)
    setError('')

    const userMessage = { id: crypto.randomUUID(), role: 'user', text }
    setMessages((prev) => [...prev, userMessage])
    setInput('')

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })
      if (!response.ok) throw new Error('Failed to send command')
      const data = await response.json()
      setRobotState(data.robot_state)
      setLogs(data.logs)
      clearAllDirty()
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: data.agent_response.user_visible_text,
          steps: data.execution_steps,
        },
      ])
      await fetchStatus()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function callManualApi(path, payload, successText, dirtyReset = null) {
    setManualBusy(true)
    setError('')
    try {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload ? JSON.stringify(payload) : undefined,
      })

      let data = {}
      try {
        data = await response.json()
      } catch {
        data = {}
      }

      if (!response.ok) {
        throw new Error(data.detail || 'Manual action failed')
      }

      setRobotState(data.robot_state)
      setLogs(data.logs)
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: 'assistant', text: successText, steps: data.steps || [] },
      ])

      if (dirtyReset === 'all') {
        clearAllDirty()
      } else if (Array.isArray(dirtyReset)) {
        clearDirtyJoints(dirtyReset)
      }

      await fetchStatus()
    } catch (err) {
      setError(err.message)
    } finally {
      setManualBusy(false)
    }
  }

  async function handleConnectHardware() {
    if (!selectedPort) {
      setError('Select a serial port first')
      return
    }
    await callManualApi(
      '/api/hardware/connect',
      { port: selectedPort, baud_rate: Number(baudRate) },
      `Connected to Arduino on ${selectedPort}`
    )
  }

  async function handleDisconnectHardware() {
    await callManualApi('/api/hardware/disconnect', {}, 'Arduino disconnected', 'all')
  }

  async function handleSendJoint(jointName) {
    const angle = Number(sliderValues[jointName])
    await callManualApi(
      '/api/manual/joint',
      { joint_name: jointName, angle },
      `Sent ${jointName} -> ${angle}°`,
      [jointName]
    )
  }

  async function handleApplyPose() {
    const joints = {}
    Object.entries(sliderValues).forEach(([joint, value]) => {
      joints[joint] = Number(value)
    })
    await callManualApi('/api/manual/pose', { joints }, 'Full pose sent', 'all')
  }

  async function handleRunPreset(preset) {
    await callManualApi(`/api/manual/preset/${preset}`, {}, `Preset ${preset} executed`, 'all')
  }

  async function handleStop() {
    await callManualApi('/api/manual/stop', {}, 'Stop signal sent', 'all')
  }

  const stateBadges = useMemo(() => {
    if (!robotState) return []
    return [
      ['Mode', robotState.mode],
      ['Hardware', robotState.hardware_connected ? 'connected' : 'disconnected'],
      ['Controller', robotState.controller_state],
      ['Pose', robotState.active_pose],
    ]
  }, [robotState])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">Local Leonardo Control</div>
          <h1>Robot Arm Console</h1>
          <p className="subtle">Минимальный интерфейс для serial, пресетов и ручного управления сервами.</p>
        </div>
        <div className="mode-picker">
          {MODE_OPTIONS.map((mode) => (
            <button
              key={mode}
              className={mode === currentMode ? 'mode-button active' : 'mode-button'}
              onClick={() => handleModeChange(mode)}
            >
              {mode}
            </button>
          ))}
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <main className="grid-layout">
        <Panel title="Status">
          <div className="badge-grid">
            {stateBadges.map(([label, value]) => (
              <Badge key={label} label={label} value={value} tone={value === 'error' ? 'danger' : 'default'} />
            ))}
          </div>

          {robotState ? (
            <div className="robot-state-card">
              <div className="card-header-inline">
                <h3>Joints</h3>
                <span className="timestamp-pill">last seen: {formatTs(robotState.last_seen_at)}</span>
              </div>

              <div className="joints-grid">
                {Object.entries(robotState.joints).map(([joint, value]) => (
                  <div key={joint} className="joint-item">
                    <span>{joint}</span>
                    <strong>{value}°</strong>
                  </div>
                ))}
              </div>

              <div className="status-grid-mini">
                <div>Port: <strong>{robotState.hardware_port || '—'}</strong></div>
                <div>Baud: <strong>{robotState.baud_rate}</strong></div>
                <div>Firmware: <strong>{robotState.firmware_ready ? 'ready' : 'not ready'}</strong></div>
                <div>Serial: <strong>{robotState.last_serial_message || '—'}</strong></div>
              </div>

              {robotState.last_error ? <div className="last-error">Last error: {robotState.last_error}</div> : null}
            </div>
          ) : null}
        </Panel>

        <Panel title="Hardware">
          <div className="hardware-stack">
            <div className="control-row">
              <label>
                Serial port
                <select value={selectedPort} onChange={(e) => setSelectedPort(e.target.value)}>
                  <option value="">Select port...</option>
                  {ports.map((port) => (
                    <option key={port.device} value={port.device}>
                      {port.device} — {port.description}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Baud rate
                <input type="number" value={baudRate} onChange={(e) => setBaudRate(e.target.value)} />
              </label>
            </div>

            <div className="button-row">
              <button className="secondary-button" onClick={fetchPorts}>Refresh ports</button>
              <button onClick={handleConnectHardware} disabled={manualBusy}>Connect</button>
              <button className="danger-button" onClick={handleDisconnectHardware} disabled={manualBusy}>Disconnect</button>
            </div>

            <div className="port-list">
              {ports.length ? ports.map((port) => (
                <div key={port.device} className="port-item">
                  <strong>{port.device}</strong>
                  <span>{port.description}</span>
                </div>
              )) : <div className="muted-block">No serial ports detected.</div>}
            </div>
          </div>
        </Panel>

        <Panel title="Manual Control">
          <div className="servo-stack">
            {Object.entries(jointLimits).map(([joint, limits]) => (
              <div key={joint} className="servo-row">
                <div className="servo-topline">
                  <strong>{joint}</strong>
                  <div className="servo-readout">
                    <span>{sliderValues[joint] ?? limits.default_angle}°</span>
                    <span className={sliderDirty[joint] ? 'draft-state dirty' : 'draft-state'}>
                      {sliderDirty[joint] ? 'draft' : 'live'}
                    </span>
                  </div>
                </div>

                <input
                  type="range"
                  min={limits.min_angle}
                  max={limits.max_angle}
                  step="1"
                  value={sliderValues[joint] ?? limits.default_angle}
                  onChange={(e) => {
                    const value = clamp(e.target.value, limits.min_angle, limits.max_angle)
                    setSliderValues((prev) => ({ ...prev, [joint]: value }))
                    markJointDirty(joint)
                  }}
                />

                <div className="servo-actions">
                  <span>{limits.min_angle}° to {limits.max_angle}°</span>
                  <span className="muted-inline">robot: {robotState?.joints?.[joint] ?? limits.default_angle}°</span>
                  <button className="tiny-button" onClick={() => handleSendJoint(joint)} disabled={manualBusy}>Send</button>
                </div>
              </div>
            ))}

            <div className="button-row">
              <button onClick={handleApplyPose} disabled={manualBusy}>Apply full pose</button>
              <button
                className="secondary-button"
                onClick={() => {
                  setSliderValues(buildInitialSliders(jointLimits, robotState))
                  clearAllDirty()
                }}
              >
                Reset sliders
              </button>
              <button className="danger-button" onClick={handleStop} disabled={manualBusy}>Stop</button>
            </div>
          </div>
        </Panel>

        <Panel title="Presets">
          <div className="preset-grid">
            {HARDWARE_PRESETS.map(([preset, label]) => (
              <button key={preset} className="preset-card" onClick={() => handleRunPreset(preset)} disabled={manualBusy}>
                <strong>{label}</strong>
                <span>{preset}</span>
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="Commands" className="chat-panel">
          <div className="messages">
            {messages.map((message) => (
              <div key={message.id} className={`message ${message.role}`}>
                <div className="message-role">{message.role === 'assistant' ? 'Agent' : 'You'}</div>
                <div className="message-text">{message.text}</div>
                {message.steps?.length ? (
                  <div className="step-list">
                    {message.steps.map((step, index) => (
                      <div key={`${message.id}-${index}`} className={`step ${step.status}`}>
                        <strong>{step.step_name}</strong>
                        <span>{step.details}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          <div className="composer">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !loading && handleSend()}
              placeholder="Например: home, открой захват, подними руку"
            />
            <button onClick={() => handleSend()} disabled={loading}>
              {loading ? '...' : 'Send'}
            </button>
          </div>

          <div className="preset-wrap">
            {PRESET_COMMANDS.map((command) => (
              <button key={command} className="chip" onClick={() => handleSend(command)} disabled={loading}>
                {command}
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="Log">
          <div className="log-list">
            {logs.map((log, index) => (
              <div key={`${log.timestamp}-${index}`} className={`log-item ${log.level}`}>
                <div className="log-topline">
                  <span>{new Date(log.timestamp).toLocaleTimeString()}</span>
                  <strong>{log.source}</strong>
                  <span className="level">{log.level}</span>
                </div>
                <div className="log-message">{log.message}</div>
                {Object.keys(log.context || {}).length ? <pre>{JSON.stringify(log.context, null, 2)}</pre> : null}
              </div>
            ))}
          </div>
        </Panel>
      </main>
    </div>
  )
}

export default App
