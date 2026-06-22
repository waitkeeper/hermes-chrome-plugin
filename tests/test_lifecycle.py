from __future__ import annotations

import importlib.util
import sys
import unittest
from unittest.mock import patch
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]


def load_plugin_module():
    package_name = "hermes_chrome_plugin_under_test"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        package_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeBridge:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeAuth:
    def __init__(self) -> None:
        self.authorized = False

    def authorize(self, _grant: str) -> None:
        self.authorized = True

    def is_authorized(self) -> bool:
        return self.authorized


class FakeContext:
    def __init__(self) -> None:
        self.hooks: list[tuple[str, object]] = []

    def register_hook(self, name: str, callback) -> None:
        self.hooks.append((name, callback))


class LifecycleRegistrationTest(unittest.TestCase):
    def test_register_uses_process_exit_cleanup_not_session_end(self) -> None:
        plugin = load_plugin_module()
        ctx = FakeContext()
        bridges: list[FakeBridge] = []
        atexit_callbacks: list[object] = []

        def fake_bridge_factory() -> FakeBridge:
            bridge = FakeBridge()
            bridges.append(bridge)
            return bridge

        with (
            patch.object(plugin, "ChromeProfileBridge", fake_bridge_factory),
            patch.object(plugin, "ChromeAuth", FakeAuth),
            patch.object(plugin, "register_all_tools", lambda *_args, **_kw: None),
            patch.object(plugin, "register_all_commands", lambda *_args, **_kw: None),
            patch.object(plugin, "_apply_standing_grant", lambda _auth: None),
            patch.object(
                plugin.atexit,
                "register",
                lambda callback: atexit_callbacks.append(callback),
            ),
        ):
            plugin.register(ctx)

        hook_names = [name for name, _callback in ctx.hooks]
        self.assertEqual(hook_names, ["pre_llm_call"])
        self.assertNotIn("on_session_end", hook_names)
        self.assertEqual(len(bridges), 1)
        self.assertEqual(atexit_callbacks, [bridges[0].stop])

    def test_manifest_declares_only_registered_lifecycle_hooks(self) -> None:
        manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")

        self.assertIn("  - pre_llm_call", manifest)
        self.assertNotIn("  - on_session_end", manifest)
        self.assertNotIn("  - on_session_finalize", manifest)


if __name__ == "__main__":
    unittest.main()
