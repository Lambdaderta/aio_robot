#include <Arduino.h>
#include <Servo.h>

struct JointConfig {
  const char* name;
  uint8_t pin;
  int logicalMin;
  int logicalMax;
  int servoMin;
  int servoMax;
  int defaultAngle;
};

// Leonardo wiring for this project:
// base     -> D3   (180° servo)
// gripper  -> D5   (90° micro servo)
// shoulder -> D13  (180° servo)
JointConfig JOINTS[] = {
  {"base", 3, 0, 180, 0, 180, 90},
  {"gripper", 5, 0, 90, 0, 90, 45},
  {"shoulder", 13, 0, 180, 0, 180, 90},
};

const int JOINT_COUNT = sizeof(JOINTS) / sizeof(JOINTS[0]);
const int STEP_DELAY_MS = 12;
const int PRESET_STEP_DELAY_MS = 10;
const int STATUS_INTERVAL_MS = 500;
const int CYCLE_MIN_ANGLE = 45;
const int CYCLE_MAX_ANGLE = 135;
const int CYCLE_STEP_DELAY_MS = 12;
const int BASE_STEP_DELAY_MS = 18;
const int BASE_CYCLE_STEP_DELAY_MS = 18;
const int SHOULDER_CYCLE_STEP_DELAY_MS = 12;

Servo SERVOS[JOINT_COUNT];
int logicalAngles[JOINT_COUNT];
bool servoAttached[JOINT_COUNT];
String controllerState = "idle";
String activePose = "boot";
String gripState = "partially_open";
bool stopRequested = false;
bool cycleEnabled = false;
int cycleTargetAngle = CYCLE_MIN_ANGLE;
unsigned long lastStatusAt = 0;
unsigned long lastCycleStepAt = 0;
unsigned long lastBaseCycleStepAt = 0;
unsigned long lastShoulderCycleStepAt = 0;

int findJointIndex(const String& name) {
  for (int i = 0; i < JOINT_COUNT; i++) {
    if (name.equalsIgnoreCase(JOINTS[i].name)) {
      return i;
    }
  }
  return -1;
}

int clampValue(int value, int minValue, int maxValue) {
  if (value < minValue) return minValue;
  if (value > maxValue) return maxValue;
  return value;
}

int logicalToServo(int jointIndex, int logicalAngle) {
  JointConfig cfg = JOINTS[jointIndex];
  long mapped = map(logicalAngle, cfg.logicalMin, cfg.logicalMax, cfg.servoMin, cfg.servoMax);
  return (int)clampValue((int)mapped, min(cfg.servoMin, cfg.servoMax), max(cfg.servoMin, cfg.servoMax));
}

void attachJointIfNeeded(int jointIndex) {
  if (jointIndex < 0 || jointIndex >= JOINT_COUNT) return;
  if (servoAttached[jointIndex]) return;

  SERVOS[jointIndex].attach(JOINTS[jointIndex].pin);
  SERVOS[jointIndex].write(logicalToServo(jointIndex, logicalAngles[jointIndex]));
  servoAttached[jointIndex] = true;
  delay(120);
}

void disableCycleMode() {
  cycleEnabled = false;
}

int effectiveStepDelayForJoint(int jointIndex, int requestedStepDelayMs) {
  if (jointIndex < 0 || jointIndex >= JOINT_COUNT) return requestedStepDelayMs;
  if (String(JOINTS[jointIndex].name) == "base") {
    return max(requestedStepDelayMs, BASE_STEP_DELAY_MS);
  }
  return requestedStepDelayMs;
}

void updateGripState() {
  int index = findJointIndex("gripper");
  if (index < 0) return;
  int value = logicalAngles[index];
  if (value <= 8) {
    gripState = "closed";
  } else if (value >= 75) {
    gripState = "open";
  } else {
    gripState = "partially_open";
  }
}

bool moveJointSmooth(int jointIndex, int logicalTarget, int stepDelayMs = STEP_DELAY_MS) {
  JointConfig cfg = JOINTS[jointIndex];
  if (logicalTarget < cfg.logicalMin || logicalTarget > cfg.logicalMax) {
    return false;
  }

  disableCycleMode();
  attachJointIfNeeded(jointIndex);
  int effectiveDelay = effectiveStepDelayForJoint(jointIndex, stepDelayMs);

  int current = logicalAngles[jointIndex];
  int direction = logicalTarget >= current ? 1 : -1;
  controllerState = "busy";

  while (current != logicalTarget) {
    if (stopRequested) {
      controllerState = "idle";
      return false;
    }
    current += direction;
    logicalAngles[jointIndex] = current;
    SERVOS[jointIndex].write(logicalToServo(jointIndex, current));
    if (String(cfg.name) == "gripper") {
      updateGripState();
    }
    delay(effectiveDelay);
  }

  controllerState = "idle";
  return true;
}

bool setJointLogical(int jointIndex, int logicalAngle, int stepDelayMs = STEP_DELAY_MS) {
  return moveJointSmooth(jointIndex, logicalAngle, stepDelayMs);
}

bool applyPose(const int targetAngles[], const char* poseName, int stepDelayMs = PRESET_STEP_DELAY_MS) {
  disableCycleMode();
  stopRequested = false;
  controllerState = "busy";

  for (int i = 0; i < JOINT_COUNT; i++) {
    if (!setJointLogical(i, targetAngles[i], stepDelayMs)) {
      controllerState = "idle";
      activePose = "stopped";
      return false;
    }
  }

  activePose = poseName;
  controllerState = "idle";
  return true;
}

bool stepJointToward(int jointIndex, int logicalTarget) {
  if (jointIndex < 0 || jointIndex >= JOINT_COUNT) return true;

  JointConfig cfg = JOINTS[jointIndex];
  int boundedTarget = clampValue(logicalTarget, cfg.logicalMin, cfg.logicalMax);
  attachJointIfNeeded(jointIndex);

  int current = logicalAngles[jointIndex];
  if (current == boundedTarget) {
    return true;
  }

  current += boundedTarget > current ? 1 : -1;
  logicalAngles[jointIndex] = current;
  SERVOS[jointIndex].write(logicalToServo(jointIndex, current));

  if (String(cfg.name) == "gripper") {
    updateGripState();
  }

  return current == boundedTarget;
}

void beginCycleMode() {
  int baseIndex = findJointIndex("base");
  int shoulderIndex = findJointIndex("shoulder");
  if (baseIndex < 0 || shoulderIndex < 0) {
    Serial.println("ERR code=CONFIG detail=cycle_joints_not_found");
    return;
  }

  stopRequested = false;
  cycleEnabled = true;
  controllerState = "busy";
  activePose = "cycle";
  lastCycleStepAt = 0;
  lastBaseCycleStepAt = 0;
  lastShoulderCycleStepAt = 0;

  attachJointIfNeeded(baseIndex);
  attachJointIfNeeded(shoulderIndex);

  int midpoint = (CYCLE_MIN_ANGLE + CYCLE_MAX_ANGLE) / 2;
  int average = (logicalAngles[baseIndex] + logicalAngles[shoulderIndex]) / 2;
  cycleTargetAngle = average > midpoint ? CYCLE_MIN_ANGLE : CYCLE_MAX_ANGLE;
}

void handleCycleTick() {
  if (!cycleEnabled) return;
  if (millis() - lastCycleStepAt < CYCLE_STEP_DELAY_MS) return;

  lastCycleStepAt = millis();

  int baseIndex = findJointIndex("base");
  int shoulderIndex = findJointIndex("shoulder");
  bool baseReady = logicalAngles[baseIndex] == cycleTargetAngle;
  bool shoulderReady = logicalAngles[shoulderIndex] == cycleTargetAngle;

  if (millis() - lastBaseCycleStepAt >= BASE_CYCLE_STEP_DELAY_MS) {
    baseReady = stepJointToward(baseIndex, cycleTargetAngle);
    lastBaseCycleStepAt = millis();
  }

  if (millis() - lastShoulderCycleStepAt >= SHOULDER_CYCLE_STEP_DELAY_MS) {
    shoulderReady = stepJointToward(shoulderIndex, cycleTargetAngle);
    lastShoulderCycleStepAt = millis();
  }

  controllerState = "busy";
  activePose = "cycle";

  if (baseReady && shoulderReady) {
    cycleTargetAngle = cycleTargetAngle == CYCLE_MIN_ANGLE ? CYCLE_MAX_ANGLE : CYCLE_MIN_ANGLE;
  }
}

void printStatus() {
  Serial.print("STATUS");
  Serial.print(" state=");
  Serial.print(controllerState);
  Serial.print(" pose=");
  Serial.print(activePose);
  Serial.print(" grip_state=");
  Serial.print(gripState);
  for (int i = 0; i < JOINT_COUNT; i++) {
    Serial.print(" ");
    Serial.print(JOINTS[i].name);
    Serial.print("=");
    Serial.print(logicalAngles[i]);
  }
  Serial.println();
}

void presetHome() {
  const int pose[JOINT_COUNT] = {90, 45, 90};
  applyPose(pose, "home");
}

void presetLift() {
  const int pose[JOINT_COUNT] = {90, 45, 130};
  applyPose(pose, "lift");
}

void presetOpen() {
  int idx = findJointIndex("gripper");
  disableCycleMode();
  stopRequested = false;
  setJointLogical(idx, 90, STEP_DELAY_MS);
  activePose = "open_gripper";
  controllerState = "idle";
}

void presetClose() {
  int idx = findJointIndex("gripper");
  disableCycleMode();
  stopRequested = false;
  setJointLogical(idx, 0, STEP_DELAY_MS);
  activePose = "close_gripper";
  controllerState = "idle";
}

void presetPark() {
  const int pose[JOINT_COUNT] = {90, 20, 40};
  applyPose(pose, "park");
}

void presetLeft() {
  const int pose[JOINT_COUNT] = {45, 45, 110};
  applyPose(pose, "left_zone");
}

void presetCenter() {
  const int pose[JOINT_COUNT] = {90, 45, 110};
  applyPose(pose, "center_zone");
}

void presetRight() {
  const int pose[JOINT_COUNT] = {135, 45, 110};
  applyPose(pose, "right_zone");
}

void presetWave() {
  disableCycleMode();
  stopRequested = false;
  presetLift();
  if (stopRequested) return;
  int baseIndex = findJointIndex("base");
  setJointLogical(baseIndex, 120, 8);
  setJointLogical(baseIndex, 60, 8);
  setJointLogical(baseIndex, 120, 8);
  setJointLogical(baseIndex, 90, 8);
  activePose = "wave";
  controllerState = "idle";
}

void presetDemo() {
  disableCycleMode();
  stopRequested = false;
  presetHome();
  if (stopRequested) return;
  presetOpen();
  if (stopRequested) return;
  presetLift();
  if (stopRequested) return;
  presetWave();
  if (stopRequested) return;
  presetClose();
  activePose = "demo";
  controllerState = "idle";
}

void handlePreset(const String& name) {
  if (name.equalsIgnoreCase("HOME")) {
    presetHome();
  } else if (name.equalsIgnoreCase("LIFT")) {
    presetLift();
  } else if (name.equalsIgnoreCase("CYCLE")) {
    beginCycleMode();
  } else if (name.equalsIgnoreCase("OPEN")) {
    presetOpen();
  } else if (name.equalsIgnoreCase("CLOSE")) {
    presetClose();
  } else if (name.equalsIgnoreCase("WAVE")) {
    presetWave();
  } else if (name.equalsIgnoreCase("DEMO")) {
    presetDemo();
  } else if (name.equalsIgnoreCase("PARK")) {
    presetPark();
  } else if (name.equalsIgnoreCase("LEFT")) {
    presetLeft();
  } else if (name.equalsIgnoreCase("CENTER")) {
    presetCenter();
  } else if (name.equalsIgnoreCase("RIGHT")) {
    presetRight();
  } else {
    Serial.println("ERR code=UNKNOWN_PRESET detail=unsupported_preset");
    return;
  }

  Serial.print("OK action=PRESET preset=");
  Serial.println(name);
}

void setup() {
  Serial.begin(115200);

  for (int i = 0; i < JOINT_COUNT; i++) {
    logicalAngles[i] = JOINTS[i].defaultAngle;
    servoAttached[i] = false;
  }

  updateGripState();
  activePose = "standby";
  lastStatusAt = millis();
  Serial.println("READY controller=robot_arm_leonardo_v1 safe_start=1");
}

void loop() {
  if (millis() - lastStatusAt > STATUS_INTERVAL_MS) {
    lastStatusAt = millis();
  }

  handleCycleTick();

  if (!Serial.available()) {
    return;
  }

  String command = Serial.readStringUntil('\n');
  command.trim();
  if (command.length() == 0) {
    return;
  }

  if (command.equalsIgnoreCase("PING")) {
    Serial.println("PONG ready=1 board=leonardo");
    return;
  }

  if (command.equalsIgnoreCase("STATUS")) {
    printStatus();
    return;
  }

  if (command.equalsIgnoreCase("STOP")) {
    stopRequested = true;
    disableCycleMode();
    controllerState = "idle";
    activePose = "stopped";
    Serial.println("OK action=STOP");
    return;
  }

  if (command.startsWith("SET ")) {
    int firstSpace = command.indexOf(' ');
    int secondSpace = command.indexOf(' ', firstSpace + 1);
    if (secondSpace < 0) {
      Serial.println("ERR code=BAD_FORMAT detail=expected_SET_joint_angle");
      return;
    }

    String jointName = command.substring(firstSpace + 1, secondSpace);
    String anglePart = command.substring(secondSpace + 1);
    int jointIndex = findJointIndex(jointName);
    if (jointIndex < 0) {
      Serial.println("ERR code=UNKNOWN_JOINT detail=joint_not_found");
      return;
    }

    int logicalAngle = anglePart.toInt();
    disableCycleMode();
    stopRequested = false;
    if (!setJointLogical(jointIndex, logicalAngle, STEP_DELAY_MS)) {
      if (logicalAngle < JOINTS[jointIndex].logicalMin || logicalAngle > JOINTS[jointIndex].logicalMax) {
        Serial.println("ERR code=LIMIT detail=angle_out_of_range");
      } else {
        Serial.println("ERR code=STOPPED detail=movement_interrupted");
      }
      return;
    }

    controllerState = "idle";
    activePose = String("joint_") + jointName;
    Serial.print("OK action=SET joint=");
    Serial.print(jointName);
    Serial.print(" angle=");
    Serial.println(logicalAngle);
    return;
  }

  if (command.startsWith("PRESET ")) {
    String presetName = command.substring(7);
    presetName.trim();
    handlePreset(presetName);
    return;
  }

  Serial.println("ERR code=UNKNOWN_COMMAND detail=unsupported_command");
}
