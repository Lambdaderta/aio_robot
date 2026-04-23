from __future__ import annotations

import unittest
from unittest.mock import PropertyMock, patch

from robot_agent_prototype_leonardo.backend.control.serial_adapter import serial_adapter
from robot_agent_prototype_leonardo.backend.control.service import ControlService
from robot_agent_prototype_leonardo.backend.state import app_state


class ControlServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ControlService()
        self.original_state = app_state.get_robot_state()
        app_state.update_robot_state(
            arm_online=True,
            controller_connected=True,
            hardware_connected=True,
            hardware_port="/dev/cu.usbmodem-test",
            baud_rate=115200,
            telemetry_source="arduino",
            controller_state="idle",
            joints={"base": 0.0, "shoulder": 90.0, "gripper": 45.0},
            last_error=None,
            last_serial_message=None,
        )

    def tearDown(self) -> None:
        app_state.update_robot_state(**self.original_state.model_dump())

    def test_base_limits_are_exposed_in_logical_space(self) -> None:
        limits = self.service.get_joint_limits()

        self.assertEqual(-90.0, limits["base"].min_angle)
        self.assertEqual(90.0, limits["base"].max_angle)
        self.assertEqual(0.0, limits["base"].default_angle)
        self.assertEqual(0.0, limits["shoulder"].min_angle)
        self.assertEqual(180.0, limits["shoulder"].max_angle)

    def test_calibrated_base_move_is_translated_before_serial_dispatch(self) -> None:
        status_payload = {
            "kind": "STATUS",
            "raw": "STATUS state=idle pose=joint_base base=180 shoulder=90 gripper=45",
            "state": "idle",
            "pose": "joint_base",
            "base": "180",
            "shoulder": "90",
            "gripper": "45",
            "grip_state": "partially_open",
        }

        with patch.object(type(serial_adapter), "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(serial_adapter, "set_joint", return_value={"raw": "OK action=SET joint=base angle=180"}) as set_joint:
                with patch.object(serial_adapter, "get_status", return_value=status_payload):
                    steps = self.service.execute_action("move_joint", {"joint_name": "base", "angle": 90})

        set_joint.assert_called_once_with("base", 180.0)
        self.assertTrue(all(step.status == "completed" for step in steps))
        self.assertEqual(90.0, app_state.get_robot_state().joints["base"])

    def test_hardware_status_is_translated_back_to_logical_angles(self) -> None:
        state = self.service._apply_hardware_status(
            {
                "raw": "STATUS state=idle pose=home base=30 shoulder=90 gripper=45",
                "state": "idle",
                "pose": "home",
                "base": "30",
                "shoulder": "90",
                "gripper": "45",
                "grip_state": "partially_open",
            }
        )

        self.assertEqual(0.0, state.joints["base"])
        self.assertEqual(90.0, state.joints["shoulder"])
        self.assertEqual(45.0, state.joints["gripper"])

    def test_hardware_minimum_base_maps_to_minus_ninety(self) -> None:
        state = self.service._apply_hardware_status(
            {
                "raw": "STATUS state=idle pose=home base=0 shoulder=90 gripper=45",
                "state": "idle",
                "pose": "home",
                "base": "0",
                "shoulder": "90",
                "gripper": "45",
                "grip_state": "partially_open",
            }
        )

        self.assertEqual(-90.0, state.joints["base"])

    def test_manual_preset_uses_calibrated_pose_not_firmware_preset(self) -> None:
        with patch.object(type(serial_adapter), "is_connected", new_callable=PropertyMock, return_value=True):
            with patch.object(serial_adapter, "set_joint", return_value={"raw": "OK"}) as set_joint:
                with patch.object(serial_adapter, "preset") as preset:
                    with patch.object(
                        serial_adapter,
                        "get_status",
                        return_value={
                            "raw": "STATUS state=idle pose=joint_right base=88.33 shoulder=90 gripper=45",
                            "state": "idle",
                            "pose": "joint_right",
                            "base": "88.33",
                            "shoulder": "90",
                            "gripper": "45",
                            "grip_state": "partially_open",
                        },
                    ):
                        self.service.execute_manual_preset("RIGHT")

        self.assertTrue(set_joint.called)
        preset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
