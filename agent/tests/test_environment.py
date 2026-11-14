import sys

import agentfield


def test_python_is_3_13_the_newest_agentfield_supports():
    assert (3, 13) <= sys.version_info < (3, 14)


def test_agentfield_exposes_the_parts_clientops_relies_on():
    agent_class = agentfield.Agent
    for capability in ("ai", "pause", "reasoner", "skill"):
        assert hasattr(agent_class, capability), capability
