import { useEffect, useRef, useState } from 'react'

import VisionControl from './VisionControl'

const DEFAULT_PRESETS = ['HOME', 'LIFT', 'CYCLE', 'OPEN', 'CLOSE', 'WAVE', 'DEMO', 'PARK', 'LEFT', 'CENTER', 'RIGHT']

const PRESET_LABELS = {
  HOME: 'Home',
  LIFT: 'Lift',
  CYCLE: 'Cycle',
  OPEN: 'Open',
  CLOSE: 'Close',
  WAVE: 'Wave',
  DEMO: 'Demo',
  PARK: 'Park',
  LEFT: 'Left',
  CENTER: 'Center',
  RIGHT: 'Right',
}

const AGENT_NEXT_STEPS = [
  'Finish vision smoothing, deadband, and calibration tuning for base and shoulder.',
  'Move pose processing off the main thread if camera latency starts to matter.',
  'Add hand-open tracking for the gripper after the arm mapping is stable.',
  'Split the CV loop into a separate service only if the local UI becomes too busy.',
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
  if (!ts) return '-'
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
  const [activity, setActivity] = useState([
    {
      id: crypto.randomUUID(),
      text: 'Hardware-only console ready. Connect Arduino before sending movement commands.',
      steps: [],
    },
  ])
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

  const sliderDirtyRef = useRef({})

  function appendActivity(text, steps = []) {
    setActivity((prev) => [...prev, { id: crypto.randomUUID(), text, steps }])
  }

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
      setLogs(data.logs || [])
      setJointLimits(data.joint_limits || {})
      setSupportedPresets(data.supported_presets?.length ? data.supported_presets : DEFAULT_PRESETS)
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
      setSelectedPort((current) => current || data.ports?.[0]?.device || '')
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

      const steps = data.steps || []
      const failedStep = steps.find((step) => step.status === 'blocked' || step.status === 'failed')
      const activityText = failedStep ? `Action did not run: ${failedStep.details}` : successText

      setRobotState(data.robot_state)
      setLogs(data.logs || [])
      appendActivity(activityText, steps)

      if (failedStep) {
        setError(failedStep.details)
      } else if (dirtyReset === 'all') {
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
      `Sent ${jointName} -> ${angle} deg`,
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

  async function submitVisionPose(joints) {
    setError('')
    try {
      const response = await fetch('/api/manual/pose', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ joints }),
      })

      let data = {}
      try {
        data = await response.json()
      } catch {
        data = {}
      }

      if (!response.ok) {
        throw new Error(data.detail || 'Vision pose failed')
      }

      setRobotState(data.robot_state)
      setLogs(data.logs || [])
      return data
    } catch (err) {
      setError(err.message)
      throw err
    }
  }

  const hardwareReady = Boolean(robotState?.hardware_connected)
  const visionLocked = visionControlActive
  const stateBadges = robotState
    ? [
        ['Hardware', hardwareReady ? 'connected' : 'offline'],
        ['Controller', robotState.controller_state],
        ['Telemetry', robotState.telemetry_source],
        ['Pose', robotState.active_pose],
      ]
    : []

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">Local Leonardo Control</div>
          <h1>Robot Arm Console</h1>
          <p className="subtle">Честный hardware-only интерфейс: serial, пресеты и ручное управление сервами.</p>
        </div>
        <div className={hardwareReady ? 'hardware-pill online' : 'hardware-pill'}>
          {hardwareReady ? 'Arduino connected' : 'Arduino offline'}
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
                    <strong>{value} deg</strong>
                  </div>
                ))}
              </div>

              <div className="status-grid-mini">
                <div>Port: <strong>{robotState.hardware_port || '-'}</strong></div>
                <div>Baud: <strong>{robotState.baud_rate}</strong></div>
                <div>Firmware: <strong>{robotState.firmware_ready ? 'ready' : 'not ready'}</strong></div>
                <div>Serial: <strong>{robotState.last_serial_message || '-'}</strong></div>
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
                      {port.device} - {port.description}
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
              <button className="danger-button" onClick={handleDisconnectHardware} disabled={manualBusy || !hardwareReady}>Disconnect</button>
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

        <VisionControl
          robotState={robotState}
          jointLimits={jointLimits}
          onSendPose={submitVisionPose}
          onControlStateChange={setVisionControlActive}
          onTrace={appendActivity}
        />

        <Panel title="Manual Control">
          <div className="servo-stack">
            {visionLocked ? (
              <div className="muted-block">Vision is steering base and shoulder. Manual sends for those joints are paused.</div>
            ) : null}

            {!hardwareReady ? (
              <div className="muted-block">Connect Arduino first. Movement buttons are disabled while offline.</div>
            ) : null}

            {Object.entries(jointLimits).map(([joint, limits]) => (
              <div key={joint} className="servo-row">
                <div className="servo-topline">
                  <strong>{joint}</strong>
                  <div className="servo-readout">
                    <span>{sliderValues[joint] ?? limits.default_angle} deg</span>
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
                  <span>{limits.min_angle} deg to {limits.max_angle} deg</span>
                  <span className="muted-inline">robot: {robotState?.joints?.[joint] ?? limits.default_angle} deg</span>
                  <button
                    className="tiny-button"
                    onClick={() => handleSendJoint(joint)}
                    disabled={manualBusy || !hardwareReady || (visionLocked && (joint === 'base' || joint === 'shoulder'))}
                  >
                    Send
                  </button>
                </div>
              </div>
            ))}

            <div className="button-row">
              <button onClick={handleApplyPose} disabled={manualBusy || !hardwareReady || visionLocked}>Apply full pose</button>
              <button
                className="secondary-button"
                onClick={() => {
                  setSliderValues(buildInitialSliders(jointLimits, robotState))
                  clearAllDirty()
                }}
              >
                Reset sliders
              </button>
              <button className="danger-button" onClick={handleStop} disabled={manualBusy || !hardwareReady}>Stop</button>
            </div>
          </div>
        </Panel>

        <Panel title="Arduino Presets">
          <div className="preset-grid">
            {supportedPresets.map((preset) => (
              <button key={preset} className="preset-card" onClick={() => handleRunPreset(preset)} disabled={manualBusy || !hardwareReady}>
                <strong>{PRESET_LABELS[preset] || preset}</strong>
                <span>{preset}</span>
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="Agent Layer">
          <div className="honest-note">
            Псевдо-чат удален. Реальный агент должен подключаться отдельным сервисом с явными tool calls, журналом решений и safety-gate перед каждым движением.
          </div>
          <div className="roadmap-list">
            {AGENT_NEXT_STEPS.map((step, index) => (
              <div key={step} className="roadmap-item">
                <span>{index + 1}</span>
                <p>{step}</p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Activity" className="chat-panel">
          <div className="messages">
            {activity.map((item) => (
              <div key={item.id} className="message assistant">
                <div className="message-role">Console</div>
                <div className="message-text">{item.text}</div>
                {item.steps?.length ? (
                  <div className="step-list">
                    {item.steps.map((step, index) => (
                      <div key={`${item.id}-${index}`} className={`step ${step.status}`}>
                        <strong>{step.step_name}</strong>
                        <span>{step.details}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
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
