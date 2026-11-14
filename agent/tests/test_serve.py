from serve import serve_options


def test_the_worker_listens_only_on_this_computer_unless_told_otherwise():
    assert serve_options({}) == {"host": "127.0.0.1", "port": 8001}


def test_the_port_and_the_address_can_be_set():
    assert serve_options({"CLIENTOPS_AGENT_PORT": "8002", "CLIENTOPS_AGENT_HOST": "0.0.0.0"}) == {"host": "0.0.0.0", "port": 8002}
