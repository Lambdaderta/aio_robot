import { useEffect, useMemo, useRef, useState } from 'react'

import {
  createVisionCalibration,
  drawVisionOverlay,
  isMeaningfulPoseChange,
  mapArmLandmarksToPose,
  smoothPose,
  VISION_DETECT_INTERVAL_MS,
  VISION_MODEL_URL,
  VISION_SEND_INTERVAL_MS,
  VISION_WASM_URL,
} from './vision'

function VisionControl({ robotState, jointLimits, onSendPose, onControlStateChange, onTrace, cameraCommand }) {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const landmarkerRef = useRef(null)
  const rafRef = useRef(0)
  const lastDetectAtRef = useRef(0)
  const lastSendAtRef = useRef(0)
  const sendingRef = useRef(false)
  const calibrationRef = useRef(null)
  const latestPoseRef = useRef(null)
  const lastSentPoseRef = useRef(null)
  const cameraStateRef = useRef('idle')
  const trackedSideRef = useRef('right')
  const jointLimitsRef = useRef(jointLimits)
  const sendPoseRef = useRef(onSendPose)
  const traceRef = useRef(onTrace)
  const autoSendRef = useRef(false)
  const controlActiveRef = useRef(false)

  const [cameraState, setCameraState] = useState('idle')
  const [visionError, setVisionError] = useState('')
  const [trackedSide, setTrackedSide] = useState('right')
  const [autoSend, setAutoSend] = useState(false)
  const [calibration, setCalibration] = useState(null)
  const [latestPose, setLatestPose] = useState(null)
  const [modelReady, setModelReady] = useState(false)

  const calibrationActive = Boolean(calibration)

  useEffect(() => {
    calibrationRef.current = calibration
  }, [calibration])

  useEffect(() => {
    trackedSideRef.current = trackedSide
  }, [trackedSide])

  useEffect(() => {
    jointLimitsRef.current = jointLimits
  }, [jointLimits])

  useEffect(() => {
    sendPoseRef.current = onSendPose
  }, [onSendPose])

  useEffect(() => {
    traceRef.current = onTrace
  }, [onTrace])

  useEffect(() => {
    cameraStateRef.current = cameraState
  }, [cameraState])

  useEffect(() => {
    autoSendRef.current = autoSend
  }, [autoSend])

  useEffect(() => {
    const nextActive = Boolean(cameraState === 'running' && autoSend && calibrationActive && latestPose?.calibrated && latestPose?.visible)
    if (controlActiveRef.current !== nextActive) {
      controlActiveRef.current = nextActive
      onControlStateChange?.(nextActive)
    }
  }, [autoSend, calibrationActive, cameraState, latestPose, onControlStateChange])

  useEffect(() => {
    return () => {
      stopCamera()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!cameraCommand?.action) return
    if (cameraCommand.action === 'open') {
      void startCamera()
      return
    }
    if (cameraCommand.action === 'close') {
      stopCamera()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraCommand?.token])

  const armHint = useMemo(() => {
    if (calibrationActive) return 'Calibration captured. Vision now follows the tracked arm and sends smoothed joint angles to the robot.'
    return 'Hold the arm upright and centered, then capture the reference pose.'
  }, [calibrationActive])

  function reportError(message) {
    setVisionError(message)
  }

  async function ensureLandmarker() {
    if (landmarkerRef.current) return landmarkerRef.current

    const { FilesetResolver, PoseLandmarker } = await import('@mediapipe/tasks-vision')
    const vision = await FilesetResolver.forVisionTasks(VISION_WASM_URL)

    landmarkerRef.current = await PoseLandmarker.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: VISION_MODEL_URL,
      },
      runningMode: 'VIDEO',
      numPoses: 1,
      minPoseDetectionConfidence: 0.5,
      minPosePresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
      outputSegmentationMasks: false,
    })

    setModelReady(true)
    return landmarkerRef.current
  }

  async function startCamera() {
    if (cameraStateRef.current === 'running' || cameraStateRef.current === 'loading') {
      return
    }

    try {
      setVisionError('')
      setCameraState('loading')
      cameraStateRef.current = 'loading'
      await ensureLandmarker()

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: 'user',
        },
      })

      streamRef.current = stream
      const video = videoRef.current
      if (!video) {
        throw new Error('Camera preview element is unavailable')
      }

      video.srcObject = stream
      await video.play()

      cameraStateRef.current = 'running'
      setCameraState('running')
      rafRef.current = window.requestAnimationFrame(renderLoop)
      onTrace?.('Vision camera started')
    } catch (error) {
      const message = error?.message || 'Failed to start the camera'
      reportError(message)
      setCameraState('error')
      stopCamera({ preserveState: true })
    }
  }

  function stopCamera(options = {}) {
    const { preserveState = false } = options

    if (rafRef.current) {
      window.cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
    }

    cameraStateRef.current = 'idle'
    lastDetectAtRef.current = 0
    sendingRef.current = false
    latestPoseRef.current = null
    lastSentPoseRef.current = null
    setLatestPose(null)

    const stream = streamRef.current
    if (stream) {
      stream.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }

    const video = videoRef.current
    if (video) {
      video.pause()
      video.srcObject = null
    }

    if (!preserveState) {
      setCameraState('idle')
    }
    if (controlActiveRef.current) {
      controlActiveRef.current = false
      onControlStateChange?.(false)
    }
  }

  function captureCalibration() {
    if (!latestPose?.visible || !latestPose.landmarks) {
      reportError('Need a visible arm before calibration')
      return
    }

    const nextCalibration = createVisionCalibration(latestPose.landmarks, trackedSideRef.current, robotState)
    setCalibration(nextCalibration)
    lastSentPoseRef.current = null
    lastSendAtRef.current = 0
    setVisionError('')
    traceRef.current?.('Vision calibration captured at the 90/90 reference pose')
  }

  async function sendPose(pose) {
    if (!pose?.calibrated) return
    if (sendingRef.current) return

    const now = performance.now()
    if (now - lastSendAtRef.current < VISION_SEND_INTERVAL_MS) {
      return
    }

    if (!isMeaningfulPoseChange(lastSentPoseRef.current, pose)) {
      return
    }

    sendingRef.current = true
    lastSendAtRef.current = now

    try {
      await sendPoseRef.current({
        base: pose.base,
        shoulder: pose.shoulder,
      })
      lastSentPoseRef.current = pose
    } catch (error) {
      reportError(error?.message || 'Failed to send vision pose')
    } finally {
      sendingRef.current = false
    }
  }

  function renderLoop() {
    rafRef.current = 0

    const video = videoRef.current
    const canvas = canvasRef.current
    const landmarker = landmarkerRef.current

    if (!video || !canvas || !landmarker || cameraStateRef.current !== 'running') {
      return
    }

    const now = performance.now()
    if (now - lastDetectAtRef.current < VISION_DETECT_INTERVAL_MS) {
      rafRef.current = window.requestAnimationFrame(renderLoop)
      return
    }
    lastDetectAtRef.current = now

    if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
      rafRef.current = window.requestAnimationFrame(renderLoop)
      return
    }

    try {
      const result = landmarker.detectForVideo(video, now)
      const landmarks = result.landmarks?.[0]
      const mappedPose = mapArmLandmarksToPose(landmarks, calibrationRef.current, trackedSideRef.current, jointLimitsRef.current)
      const nextPose = smoothPose(latestPoseRef.current, mappedPose, 0.34)

      latestPoseRef.current = nextPose
      setLatestPose(nextPose)
      drawVisionOverlay(canvas, video, nextPose, calibrationRef.current)

      if (autoSendRef.current && nextPose?.calibrated) {
        void sendPose(nextPose)
      }
    } catch (error) {
      reportError(error?.message || 'Vision inference failed')
    }

    if (cameraStateRef.current === 'running') {
      rafRef.current = window.requestAnimationFrame(renderLoop)
    }
  }

  return (
    <section className="panel vision-panel">
      <div className="panel-header">
        <h2>Vision Control</h2>
      </div>

      <div className="panel-body">
        <div className="vision-stage">
          <div className="vision-viewer">
            <video ref={videoRef} className="vision-video" autoPlay muted playsInline />
            <canvas ref={canvasRef} className="vision-canvas" />
          </div>

          <div className="vision-controls">
            <div className="vision-action-row">
              <button onClick={startCamera} disabled={cameraState === 'loading' || cameraState === 'running'}>
                Start camera
              </button>
              <button className="secondary-button" onClick={stopCamera} disabled={cameraState === 'idle'}>
                Stop camera
              </button>
              <button className="secondary-button" onClick={captureCalibration} disabled={!latestPose?.visible}>
                Capture calibration
              </button>
              <button
                className="secondary-button"
                onClick={() => sendPose(latestPose)}
                disabled={!latestPose?.calibrated || autoSend}
              >
                Send current pose
              </button>
            </div>

            <div className="vision-select-row">
              <label>
                Arm side
                <select
                  value={trackedSide}
                  onChange={(event) => {
                    const nextValue = event.target.value
                    trackedSideRef.current = nextValue
                    setTrackedSide(nextValue)
                    setCalibration(null)
                    latestPoseRef.current = null
                    setLatestPose(null)
                    lastSentPoseRef.current = null
                    lastSendAtRef.current = 0
                  }}
                >
                  <option value="right">Right arm</option>
                  <option value="left">Left arm</option>
                </select>
              </label>

              <label className="vision-switch">
                <input
                  type="checkbox"
                  checked={autoSend}
                  onChange={(event) => {
                    const nextValue = event.target.checked
                    autoSendRef.current = nextValue
                    setAutoSend(nextValue)
                    if (nextValue) {
                      lastSentPoseRef.current = null
                      lastSendAtRef.current = 0
                    }
                  }}
                />
                <span>Send to robot</span>
              </label>
            </div>

            <div className="vision-status-grid">
              <div>
                <span className="vision-status-label">Camera</span>
                <strong>{cameraState}</strong>
              </div>
              <div>
                <span className="vision-status-label">Model</span>
                <strong>{modelReady ? 'ready' : 'loading on demand'}</strong>
              </div>
              <div>
                <span className="vision-status-label">Mode</span>
                <strong>{latestPose?.calibrated ? 'mapped' : 'preview'}</strong>
              </div>
              <div>
                <span className="vision-status-label">Control</span>
                <strong>{autoSend && latestPose?.calibrated ? 'armed' : 'manual'}</strong>
              </div>
            </div>

            <div className="vision-pose-grid">
              <div>
                <span className="vision-status-label">Base</span>
                <strong>{latestPose?.base ?? robotState?.joints?.base ?? '-'}</strong>
              </div>
              <div>
                <span className="vision-status-label">Shoulder</span>
                <strong>{latestPose?.shoulder ?? robotState?.joints?.shoulder ?? '-'}</strong>
              </div>
              <div>
                <span className="vision-status-label">Confidence</span>
                <strong>{latestPose?.confidence ?? '-'}</strong>
              </div>
              <div>
                <span className="vision-status-label">Arm span</span>
                <strong>{latestPose?.armSpan ?? '-'}</strong>
              </div>
            </div>

            <div className="vision-hint">
              {armHint}
            </div>

            {visionError ? <div className="vision-error">{visionError}</div> : null}
          </div>
        </div>
      </div>
    </section>
  )
}

export default VisionControl
