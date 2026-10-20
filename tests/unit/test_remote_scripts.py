import ast
import subprocess
import unittest
from importlib.resources import files

from fck_rkn.remote import render


class ScriptPlatformTests(unittest.TestCase):
    def test_scripts_do_not_reference_a_cloud_provider(self) -> None:
        forbidden = ("google", "gcloud", "metadata", "gcp", "iap", "aws", "azure")
        for path in (files("fck_rkn.remote") / "scripts").iterdir():
            text = path.read_text().lower()
            for word in forbidden:
                self.assertNotIn(word, text, f"{path.name} mentions {word}")


class LoadOutlineImageTests(unittest.TestCase):
    def test_loads_archive_from_stdin_only_when_image_is_missing(self) -> None:
        script = render("load_outline_image.sh", image="img:tag")
        self.assertIn("expected_image=img:tag", script)
        self.assertLess(
            script.index("docker image inspect"), script.index("docker load")
        )
        self.assertIn("skipping the load", script)
        self.assertIn("gunzip | sudo docker load", script)
        self.assertNotIn("docker build", script)


class RenderTests(unittest.TestCase):
    def test_fills_placeholders_and_expands_includes(self) -> None:
        script = render("deploy_squid.sh", container_name="c", image="i", port=1)
        self.assertIn("container_name=c", script)
        self.assertIn("apt-get install -y docker.io curl", script)
        self.assertNotIn("#include", script)
        self.assertNotIn("#!", script)

    def test_rejects_missing_and_unused_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "no value for @IMAGE@"):
            render("deploy_squid.sh", container_name="c", port=1)
        with self.assertRaisesRegex(ValueError, "does not use: EXTRA"):
            render("deploy_squid.sh", container_name="c", image="i", port=1, extra="x")

    def test_values_are_not_expanded_twice(self) -> None:
        script = render("deploy_squid.sh", container_name="@IMAGE@", image="i", port=1)
        self.assertIn("container_name=@IMAGE@", script)

    def test_python_scripts_are_valid_python(self) -> None:
        for name in ("create_outline_user.py", "squid_http_check.py"):
            source = (files("fck_rkn.remote") / "scripts" / name).read_text()
            ast.parse(source)

    def test_shell_scripts_are_valid_bash(self) -> None:
        scripts = files("fck_rkn.remote") / "scripts"
        for entry in scripts.iterdir():
            if entry.name.endswith(".sh") and entry.name != "docker_setup.sh":
                with self.subTest(script=entry.name):
                    text = entry.read_text()
                    text = text.replace("#include docker_setup.sh", "true")
                    for include in ("create_outline_user.py", "squid_http_check.py",
                                    "outline_shadowbox.Dockerfile"):
                        text = text.replace(f"#include {include}", "")
                    result = subprocess.run(
                        ["bash", "-n"], input=text, text=True, capture_output=True
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
