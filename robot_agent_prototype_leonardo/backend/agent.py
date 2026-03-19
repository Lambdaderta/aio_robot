from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .models import AgentResponse, ParsedIntent, SkillCall
from .router import skill_router
from .skills.robot_arm import robot_arm_skill
from .state import app_state


@dataclass
class AgentOutcome:
    parsed_intent: ParsedIntent
    skill_call: SkillCall | None
    response: AgentResponse
    execution_steps: list


class PseudoAgent:
    def parse_intent(self, message: str) -> ParsedIntent:
        text = message.lower().strip()

        joint_patterns = {
            "перв": "base",
            "баз": "base",
            "основан": "base",
            "втор": "shoulder",
            "плеч": "shoulder",
            "рук": "shoulder",
            "захват": "gripper",
            "клеш": "gripper",
            "gripper": "gripper",
            "base": "base",
            "shoulder": "shoulder",
        }

        if any(k in text for k in ["статус", "состоя", "готов"]):
            return ParsedIntent(intent_name="status", confidence=0.97)
        if any(k in text for k in ["дом", "home"]):
            return ParsedIntent(intent_name="home", confidence=0.95)
        if any(k in text for k in ["открой захват", "раскрой захват", "open gripper"]):
            return ParsedIntent(intent_name="open_gripper", confidence=0.96)
        if any(k in text for k in ["закрой захват", "сожми захват", "close gripper"]):
            return ParsedIntent(intent_name="close_gripper", confidence=0.96)
        if any(k in text for k in ["подними", "верхнюю позицию", "safe upper", "lift"]):
            return ParsedIntent(intent_name="lift", confidence=0.91)
        if any(k in text for k in ["цикл", "качай", "sweep", "scan"]):
            return ParsedIntent(intent_name="cycle", confidence=0.93)
        if any(k in text for k in ["приветств", "wave", "приветствие"]):
            return ParsedIntent(intent_name="wave", confidence=0.94)
        if any(k in text for k in ["демонстрац", "demo", "сценарий"]):
            return ParsedIntent(intent_name="demo", confidence=0.95)
        if any(k in text for k in ["останов", "stop"]):
            return ParsedIntent(intent_name="stop", confidence=0.98)
        if any(k in text for k in ["припарку", "park"]):
            return ParsedIntent(intent_name="park", confidence=0.93)

        if "перемести" in text and any(zone in text for zone in ["лев", "центр", "прав"]):
            if "лев" in text:
                zone = "left"
            elif "прав" in text:
                zone = "right"
            else:
                zone = "center"
            return ParsedIntent(intent_name="move_zone", confidence=0.88, entities={"zone": zone})

        if any(k in text for k in ["поверни", "сустав", "joint", "угол"]):
            joint_name = None
            for key, value in joint_patterns.items():
                if key in text:
                    joint_name = value
                    break
            joint_name = joint_name or "shoulder"
            match = re.search(r"(-?\d+(?:[\.,]\d+)?)", text)
            angle = None
            if match:
                angle = float(match.group(1).replace(",", "."))
            entities = {"joint_name": joint_name}
            if angle is not None:
                entities["angle"] = angle
            return ParsedIntent(intent_name="move_joint", confidence=0.83, entities=entities)

        if "перемести объект" in text or "возьми объект" in text:
            return ParsedIntent(
                intent_name="move_zone",
                confidence=0.62,
                entities={"zone": "center"},
                requires_confirmation=True,
            )

        return ParsedIntent(
            intent_name="unknown",
            confidence=0.25,
            is_safe=True,
            safety_message="Command was not mapped to a supported skill",
        )

    def compose_response(self, parsed: ParsedIntent, skill_call: SkillCall | None, blocked_reason: str | None = None) -> AgentResponse:
        state = app_state.get_robot_state()

        if blocked_reason:
            return AgentResponse(
                user_visible_text=(
                    f"Команда отклонена модулем безопасности. Причина: {blocked_reason}. "
                    "Система сохранила текущее состояние манипулятора без изменений."
                ),
                internal_reasoning_summary="Intent recognized, but validation failed in skill safety layer.",
                selected_skill=skill_call.skill_name if skill_call else None,
                selected_action=skill_call.action_name if skill_call else None,
                suggested_next_action="Проверь допустимый диапазон углов или выбери безопасный preset.",
            )

        if parsed.intent_name == "unknown":
            return AgentResponse(
                user_visible_text=(
                    "Я не смог уверенно сопоставить команду с доступным навыком манипулятора. "
                    "Попробуй одну из поддерживаемых команд: статус, домой, открыть захват, демонстрация, приветствие."
                ),
                internal_reasoning_summary="No skill matched the command with sufficient confidence.",
                suggested_next_action="Спроси про статус системы или запусти демонстрационный сценарий.",
            )

        if parsed.requires_confirmation:
            return AgentResponse(
                user_visible_text=(
                    "Я распознал задачу уровня manipulation-task, но параметров пока недостаточно. "
                    "Уточни целевую зону: левая, правая или центральная."
                ),
                internal_reasoning_summary="High-level task detected, awaiting missing parameters.",
                suggested_next_action="Например: перемести объект в левую зону.",
            )

        action = skill_call.action_name if skill_call else None

        variants = {
            "get_status": [
                f"Манипулятор доступен, режим исполнения — {state.mode}. Телеметрия идёт из {state.telemetry_source}. Контроллер: {state.controller_state}.",
                f"Система на связи. Текущий режим: {state.mode}. Активная поза: {state.active_pose}. Hardware link: {'connected' if state.hardware_connected else 'disconnected'}.",
            ],
            "home": [
                "Команда распознана как переход в домашнюю позицию. Проверка ограничений завершена, передаю задачу в модуль управления.",
                "Выполняю safe-home routine. Целевые углы будут сведены к стартовым значениям для Leonardo-конфига.",
            ],
            "open_gripper": [
                "Запрошено раскрытие захвата. Передаю действие в исполнительный слой и жду подтверждение контроллера.",
                "Распознал команду на открытие захвата. Формирую короткую motion-команду для gripper servo.",
            ],
            "close_gripper": [
                "Выполняю закрытие захвата. Контролирую, чтобы диапазон оставался в пределах лимитов микросервы.",
                "Запрошено сжатие захвата. Передаю команду в skill robot_arm.",
            ],
            "lift_pose": [
                "Команда интерпретирована как переход в верхнюю безопасную позу. Проверка ограничений пройдена, сценарий допустим.",
                "Запускаю подъем руки в сохранённый lift-preset."
            ],
            "cycle_motion": [
                "Запускаю плавный цикл для base и shoulder в диапазоне 45..135. Остановить можно командой stop.",
                "Перевожу руку в режим непрерывного качания base и shoulder. Для остановки используй stop.",
            ],
            "wave": [
                "Запускаю приветственное микродвижение манипулятора. Сценарий выбран из библиотеки жестов.",
                "Распознана команда приветствия. Выполняю демонстрационный жест через skill robot_arm.",
            ],
            "demo_sequence": [
                "Выбран демонстрационный сценарий из нескольких последовательных движений. Готовлю multi-step motion plan.",
                "Запускаю демонстрационную последовательность: переход в базовую позу, жест, работа захватом.",
            ],
            "stop": [
                "Передаю сигнал остановки в контроллер. Система перейдёт в безопасное состояние без изменения аппаратных лимитов.",
                "Запрошена остановка активной задачи. Прерываю текущую motion sequence.",
            ],
            "park": [
                "Запускаю безопасную процедуру парковки манипулятора. Цель — минимизировать риск лишнего движения.",
                "Команда распознана как safe-park routine. Передаю задачу в исполнительный модуль.",
            ],
            "move_zone": [
                f"Распознана задача перемещения в рабочую зону '{skill_call.parameters.get('zone')}'. Формирую соответствующий сценарий выравнивания.",
                f"Подготовил transfer-command для зоны '{skill_call.parameters.get('zone')}'. Проверка параметров завершена.",
            ],
            "move_joint": [
                f"Распознано суставное управление: {skill_call.parameters.get('joint_name')} -> {skill_call.parameters.get('angle')}°. Передаю команду в skill layer.",
                f"Команда интерпретирована как прямое управление суставом {skill_call.parameters.get('joint_name')}. Выполняю после проверки ограничений.",
            ],
        }

        user_text = random.choice(variants.get(action, ["Команда принята, запускаю обработку."]))
        return AgentResponse(
            user_visible_text=user_text,
            internal_reasoning_summary="Intent parsed, skill selected, parameters validated, execution requested.",
            selected_skill=skill_call.skill_name if skill_call else None,
            selected_action=action,
            suggested_next_action="После выполнения можно запросить статус системы или запустить следующую команду.",
        )

    def handle_message(self, message: str) -> AgentOutcome:
        app_state.add_log("agent", "User message received", context={"message": message})
        parsed = self.parse_intent(message)
        app_state.add_log("agent", "Intent parsed", context={"intent": parsed.intent_name, "confidence": parsed.confidence, "entities": parsed.entities})

        skill_call = skill_router.route(parsed.intent_name, parsed.entities)
        if skill_call:
            app_state.add_log("router", "Skill selected", context={"skill": skill_call.skill_name, "action": skill_call.action_name})

        if parsed.intent_name == "unknown" or parsed.requires_confirmation or not skill_call:
            response = self.compose_response(parsed, skill_call)
            return AgentOutcome(parsed_intent=parsed, skill_call=skill_call, response=response, execution_steps=[])

        valid, error = robot_arm_skill.validate_action(skill_call)
        if not valid:
            response = self.compose_response(parsed, skill_call, blocked_reason=error)
            return AgentOutcome(parsed_intent=parsed, skill_call=skill_call, response=response, execution_steps=[])

        response = self.compose_response(parsed, skill_call)
        execution_steps = robot_arm_skill.handle_action(skill_call)
        return AgentOutcome(parsed_intent=parsed, skill_call=skill_call, response=response, execution_steps=execution_steps)


pseudo_agent = PseudoAgent()
