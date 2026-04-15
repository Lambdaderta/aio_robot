export const VISION_WASM_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm'

export const VISION_MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task'

export const VISION_MIN_VISIBILITY = 0.45
export const VISION_BASE_GAIN = 100
export const VISION_SHOULDER_GAIN = 100
export const VISION_SEND_DEADBAND_DEG = 2.5
export const VISION_SEND_INTERVAL_MS = 800
export const VISION_DETECT_INTERVAL_MS = 80

const ARM_INDEXES = {
  left: {
    shoulder: 11,
    elbow: 13,
    wrist: 15,
  },
  right: {
    shoulder: 12,
    elbow: 14,
    wrist: 16,
  },
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

export function round(value, digits = 1) {
  const factor = 10 ** digits
  return Math.round(Number(value) * factor) / factor
}

export function distance(a, b) {
  if (!a || !b) return 0
  return Math.hypot((a.x ?? 0) - (b.x ?? 0), (a.y ?? 0) - (b.y ?? 0))
}

export function normalizeLandmark(landmark) {
  return {
    x: Number(landmark?.x ?? 0),
    y: Number(landmark?.y ?? 0),
    z: Number(landmark?.z ?? 0),
    visibility: Number(landmark?.visibility ?? 0),
  }
}

export function selectArmLandmarks(landmarks, side) {
  const indexes = ARM_INDEXES[side] || ARM_INDEXES.right
  return {
    shoulder: normalizeLandmark(landmarks?.[indexes.shoulder]),
    elbow: normalizeLandmark(landmarks?.[indexes.elbow]),
    wrist: normalizeLandmark(landmarks?.[indexes.wrist]),
  }
}

export function createVisionCalibration(landmarks, side, robotState) {
  const arm = selectArmLandmarks(landmarks, side)
  const armSpan = Math.max(distance(arm.shoulder, arm.wrist), 0.0001)

  return {
    side: side === 'left' ? 'left' : 'right',
    base: Number(robotState?.joints?.base ?? 90),
    shoulder: Number(robotState?.joints?.shoulder ?? 90),
    armSpan,
    landmarks: arm,
    shoulderPoint: arm.shoulder,
    elbowPoint: arm.elbow,
    wristPoint: arm.wrist,
    calibratedAt: Date.now(),
  }
}

export function mapArmLandmarksToPose(landmarks, calibration, side, jointLimits = {}) {
  const arm = selectArmLandmarks(landmarks, side || calibration?.side || 'right')
  const visibility = Math.min(arm.shoulder.visibility, arm.elbow.visibility, arm.wrist.visibility)

  if (!Number.isFinite(visibility) || visibility < VISION_MIN_VISIBILITY) {
    return {
      visible: false,
      calibrated: false,
      confidence: round(Number.isFinite(visibility) ? visibility : 0, 2),
      landmarks: arm,
    }
  }

  if (!calibration) {
    return {
      visible: true,
      calibrated: false,
      confidence: round(visibility, 2),
      landmarks: arm,
    }
  }

  const armSpan = calibration.armSpan || distance(calibration.shoulderPoint, calibration.wristPoint) || 0.0001
  const baseOffset =
    ((arm.elbow.x - calibration.elbowPoint.x) + (arm.wrist.x - calibration.wristPoint.x)) / (2 * armSpan)
  const shoulderOffset =
    ((calibration.elbowPoint.y - arm.elbow.y) + (calibration.wristPoint.y - arm.wrist.y)) / (2 * armSpan)

  const baseLimits = jointLimits.base || { min_angle: 0, max_angle: 180 }
  const shoulderLimits = jointLimits.shoulder || { min_angle: 0, max_angle: 180 }

  const base = clamp(
    round(calibration.base + baseOffset * VISION_BASE_GAIN, 1),
    baseLimits.min_angle,
    baseLimits.max_angle
  )
  const shoulder = clamp(
    round(calibration.shoulder + shoulderOffset * VISION_SHOULDER_GAIN, 1),
    shoulderLimits.min_angle,
    shoulderLimits.max_angle
  )

  return {
    visible: true,
    calibrated: true,
    confidence: round(visibility, 2),
    base,
    shoulder,
    baseOffset: round(baseOffset, 3),
    shoulderOffset: round(shoulderOffset, 3),
    armSpan: round(armSpan, 3),
    landmarks: arm,
  }
}

export function smoothPose(previousPose, nextPose, alpha = 0.28) {
  if (!previousPose || !previousPose.calibrated || !nextPose?.calibrated) {
    return nextPose
  }

  return {
    ...nextPose,
    base: round(previousPose.base + (nextPose.base - previousPose.base) * alpha, 1),
    shoulder: round(previousPose.shoulder + (nextPose.shoulder - previousPose.shoulder) * alpha, 1),
  }
}

export function isMeaningfulPoseChange(previousPose, nextPose, deadband = VISION_SEND_DEADBAND_DEG) {
  if (!previousPose || !nextPose) return true
  if (!previousPose.calibrated || !nextPose.calibrated) return false

  return (
    Math.abs(Number(previousPose.base ?? 0) - Number(nextPose.base ?? 0)) >= deadband ||
    Math.abs(Number(previousPose.shoulder ?? 0) - Number(nextPose.shoulder ?? 0)) >= deadband
  )
}

function drawDot(ctx, point, color, radius) {
  ctx.beginPath()
  ctx.fillStyle = color
  ctx.arc(point.x * ctx.canvas.width, point.y * ctx.canvas.height, radius, 0, Math.PI * 2)
  ctx.fill()
}

function drawLine(ctx, from, to, color, width) {
  ctx.beginPath()
  ctx.strokeStyle = color
  ctx.lineWidth = width
  ctx.moveTo(from.x * ctx.canvas.width, from.y * ctx.canvas.height)
  ctx.lineTo(to.x * ctx.canvas.width, to.y * ctx.canvas.height)
  ctx.stroke()
}

function drawLabel(ctx, text, x, y) {
  const paddingX = 8
  ctx.font = '12px system-ui, sans-serif'
  const width = ctx.measureText(text).width + paddingX * 2
  ctx.fillStyle = 'rgba(0, 0, 0, 0.65)'
  ctx.fillRect(x, y - 16, width, 22)
  ctx.fillStyle = '#f3f3f3'
  ctx.fillText(text, x + paddingX, y)
}

export function drawVisionOverlay(canvas, video, pose, calibration) {
  if (!canvas || !video) return

  const width = video.videoWidth || 0
  const height = video.videoHeight || 0
  if (!width || !height) return

  if (canvas.width !== width) canvas.width = width
  if (canvas.height !== height) canvas.height = height

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.clearRect(0, 0, width, height)

  if (calibration?.landmarks) {
    drawLine(ctx, calibration.landmarks.shoulder, calibration.landmarks.elbow, 'rgba(255, 255, 255, 0.18)', 5)
    drawLine(ctx, calibration.landmarks.elbow, calibration.landmarks.wrist, 'rgba(255, 255, 255, 0.18)', 5)
    drawDot(ctx, calibration.landmarks.shoulder, 'rgba(255, 255, 255, 0.35)', 5)
    drawDot(ctx, calibration.landmarks.elbow, 'rgba(255, 255, 255, 0.35)', 5)
    drawDot(ctx, calibration.landmarks.wrist, 'rgba(255, 255, 255, 0.35)', 6)
  }

  if (pose?.landmarks) {
    drawLine(ctx, pose.landmarks.shoulder, pose.landmarks.elbow, '#8bd3ff', 5)
    drawLine(ctx, pose.landmarks.elbow, pose.landmarks.wrist, '#8bd3ff', 5)
    drawDot(ctx, pose.landmarks.shoulder, '#8bd3ff', 5)
    drawDot(ctx, pose.landmarks.elbow, '#ffd37a', 5)
    drawDot(ctx, pose.landmarks.wrist, '#9cf0c5', 6)
  }

  if (pose?.calibrated) {
    drawLabel(
      ctx,
      `base ${pose.base.toFixed(1)} shoulder ${pose.shoulder.toFixed(1)} conf ${pose.confidence.toFixed(2)}`,
      16,
      28
    )
  } else if (pose?.visible) {
    drawLabel(ctx, `pose visible conf ${pose.confidence.toFixed(2)}`, 16, 28)
  } else {
    drawLabel(ctx, 'no pose detected', 16, 28)
  }
}
