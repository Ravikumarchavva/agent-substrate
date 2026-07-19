"""Static code-safety classifier — exploratory analysis is SAFE (no approval),
dangerous operations are CRITICAL with a reason list feeding the summary."""

from __future__ import annotations

from unittest.mock import AsyncMock

from substrate.capabilities.tools.code_interpreter.code_interpreter.code_risk import (
    classify_and_summarize,
    classify_code,
    templated_summary,
)
from substrate.kernel.core.content import TextBlock
from substrate.kernel.tools.tools import ToolRisk


# ── SAFE: exploratory analysis ────────────────────────────────────────────────


def test_pandas_matplotlib_is_safe():
    code = (
        "import pandas as pd\n"
        "import matplotlib.pyplot as plt\n"
        "df = pd.read_excel('data.xlsx')\n"
        "df.describe()\n"
        "plt.plot(df['a'], df['b'])\n"
        "plt.savefig('chart.png')\n"
    )
    risk, reasons = classify_code(code)
    assert risk == ToolRisk.SAFE
    assert reasons == []


def test_listdir_and_read_is_safe():
    code = "import os\nprint(os.listdir('.'))\nopen('report.csv').read()\n"
    risk, _ = classify_code(code)
    assert risk == ToolRisk.SAFE


def test_in_workspace_write_is_safe():
    code = "open('out.csv', 'w').write('a,b\\n')\n"
    risk, _ = classify_code(code)
    assert risk == ToolRisk.SAFE


def test_syntax_error_is_safe():
    # Can't execute a dangerous op if it won't parse.
    risk, reasons = classify_code("this is not python !!!")
    assert risk == ToolRisk.SAFE
    assert reasons == []


# ── CRITICAL: dangerous operations ────────────────────────────────────────────


def test_subprocess_is_critical():
    risk, reasons = classify_code("import subprocess\nsubprocess.run(['ls'])\n")
    assert risk == ToolRisk.CRITICAL
    assert "runs shell/subprocess commands" in reasons


def test_os_system_is_critical():
    risk, reasons = classify_code("import os\nos.system('rm -rf /')\n")
    assert risk == ToolRisk.CRITICAL
    assert "runs shell/subprocess commands" in reasons


def test_shutil_rmtree_is_critical():
    risk, reasons = classify_code("import shutil\nshutil.rmtree('/data')\n")
    assert risk == ToolRisk.CRITICAL
    assert "deletes files" in reasons


def test_os_remove_is_critical():
    risk, reasons = classify_code("import os\nos.remove('important.db')\n")
    assert risk == ToolRisk.CRITICAL
    assert "deletes files" in reasons


def test_network_request_is_critical():
    risk, reasons = classify_code("import requests\nrequests.get('http://x.com')\n")
    assert risk == ToolRisk.CRITICAL
    assert "makes network connections" in reasons


def test_dynamic_exec_is_critical():
    risk, reasons = classify_code("exec('print(1)')\n")
    assert risk == ToolRisk.CRITICAL
    assert "executes dynamically-constructed code" in reasons


def test_out_of_workspace_write_is_critical():
    risk, reasons = classify_code("open('/etc/passwd', 'w').write('x')\n")
    assert risk == ToolRisk.CRITICAL
    assert "writes files outside the workspace" in reasons


def test_multiple_reasons_collected():
    code = "import os\nimport requests\nos.remove('a')\nrequests.get('u')\n"
    risk, reasons = classify_code(code)
    assert risk == ToolRisk.CRITICAL
    assert "deletes files" in reasons
    assert "makes network connections" in reasons


# ── templated summary ─────────────────────────────────────────────────────────


def test_templated_summary_single():
    assert templated_summary(["deletes files"]) == "This code deletes files."


def test_templated_summary_multiple():
    out = templated_summary(["deletes files", "makes network connections"])
    assert out == "This code deletes files, and makes network connections."


# ── hybrid classify_and_summarize ─────────────────────────────────────────────


async def test_safe_never_calls_llm():
    client = AsyncMock()
    risk, summary = await classify_and_summarize(
        "import pandas as pd\ndf = pd.read_csv('x.csv')\n", client
    )
    assert risk == ToolRisk.SAFE
    assert summary is None
    client.generate.assert_not_awaited()


async def test_critical_uses_llm_summary_when_available():
    client = AsyncMock()
    client.generate.return_value = type(
        "R", (), {"content": [TextBlock(text="Deletes the database file.")]}
    )()
    risk, summary = await classify_and_summarize(
        "import os\nos.remove('db.sqlite')\n", client
    )
    assert risk == ToolRisk.CRITICAL
    assert summary == "Deletes the database file."
    client.generate.assert_awaited_once()


async def test_critical_falls_back_to_templated_without_client():
    risk, summary = await classify_and_summarize(
        "import shutil\nshutil.rmtree('/data')\n", None
    )
    assert risk == ToolRisk.CRITICAL
    assert summary == "This code deletes files."


async def test_critical_falls_back_when_llm_raises():
    client = AsyncMock()
    client.generate.side_effect = RuntimeError("llm down")
    risk, summary = await classify_and_summarize("import os\nos.system('x')\n", client)
    assert risk == ToolRisk.CRITICAL
    assert summary == "This code runs shell/subprocess commands."
