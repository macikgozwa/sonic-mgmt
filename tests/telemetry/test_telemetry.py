import logging
import re
import pytest

from tests.common.helpers.assertions import pytest_assert
from tests.common.utilities import wait_until, wait_tcp_connection

pytestmark = [
    pytest.mark.topology('any')
]

logger = logging.getLogger(__name__)

TELEMETRY_PORT = 50051
mode_subscribe = "subscribe"
mode_get = "get"

subscribe_mode_stream = 0
subscribe_mode_once = 1
subscribe_mode_poll = 2

submode_targetdefined=0
submode_onchange=1
submode_sample=2

# Helper functions
def get_dict_stdout(gnmi_out, certs_out):
    """ Extracts dictionary from redis output.
    """
    gnmi_list = []
    gnmi_list = get_list_stdout(gnmi_out) + get_list_stdout(certs_out)
    # Elements in list alternate between key and value. Separate them and combine into a dict.
    key_list = gnmi_list[0::2]
    value_list = gnmi_list[1::2]
    params_dict = dict(zip(key_list, value_list))
    return params_dict

def get_list_stdout(cmd_out):
    out_list = []
    for x in cmd_out:
        result = x.encode('UTF-8')
        out_list.append(result)
    return out_list

def setup_telemetry_forpyclient(duthost):
    """ Set client_auth=false. This is needed for pyclient to sucessfully set up channel with gnmi server.
        Restart telemetry process
    """
    client_auth_out = duthost.shell('sonic-db-cli CONFIG_DB HGET "TELEMETRY|gnmi" "client_auth"', module_ignore_errors=False)['stdout_lines']
    client_auth = str(client_auth_out[0])
    if client_auth == "true":
        duthost.shell('sonic-db-cli CONFIG_DB HSET "TELEMETRY|gnmi" "client_auth" "false"', module_ignore_errors=False)
        duthost.service(name="telemetry", state="restarted")
    else:
        logger.info('client auth is false. No need to restart telemetry')

def generate_client_cli(duthost, mode=mode_get, xpath="COUNTERS/Ethernet0", target="COUNTERS_DB", subscribe_mode=subscribe_mode_stream, submode=submode_sample, intervalms=0, update_count=3):
    """Generate the py_gnmicli command line based on the given params.
    """
    cmdFormat = 'python /gnxi/gnmi_cli_py/py_gnmicli.py -g -t {0} -p {1} -m {2} -x {3} -xt {4} -o {5}'
    cmd = cmdFormat.format(duthost.mgmt_ip, TELEMETRY_PORT, mode, xpath, target, "ndastreamingservertest")

    if mode == mode_subscribe:
        cmd += " --subscribe_mode {0} --submode {1} --interval {2} --update_count {3}".format(subscribe_mode, submode, intervalms, update_count)
    return cmd

def assert_equal(actual, expected, message):
    """Helper method to compare an expected value vs the actual value.
    """
    pytest_assert(actual == expected, "{0}. Expected {1} vs actual {2}".format(message, expected, actual))

def verify_telemetry_dockerimage(duthost):
    """If telemetry docker is available in image then return true
    """
    docker_out_list = []
    docker_out = duthost.shell('docker images docker-sonic-telemetry', module_ignore_errors=False)['stdout_lines']
    docker_out_list = get_list_stdout(docker_out)
    matching = [s for s in docker_out_list if "docker-sonic-telemetry" in s]
    return (len(matching) > 0)

# Test functions
def test_config_db_parameters(duthost):
    """Verifies required telemetry parameters from config_db.
    """
    docker_present = verify_telemetry_dockerimage(duthost)
    if not docker_present:
        pytest.skip("docker-sonic-telemetry is not part of the image")

    gnmi = duthost.shell('sonic-db-cli CONFIG_DB HGETALL "TELEMETRY|gnmi"', module_ignore_errors=False)['stdout_lines']
    pytest_assert(gnmi is not None, "TELEMETRY|gnmi does not exist in config_db")

    certs = duthost.shell('sonic-db-cli CONFIG_DB HGETALL "TELEMETRY|certs"', module_ignore_errors=False)['stdout_lines']
    pytest_assert(certs is not None, "TELEMETRY|certs does not exist in config_db")

    d = get_dict_stdout(gnmi, certs)
    for key, value in d.items():
        if str(key) == "port":
            port_expected = str(TELEMETRY_PORT)
            pytest_assert(str(value) == port_expected, "'port' value is not '{}'".format(port_expected))
        if str(key) == "ca_crt":
            ca_crt_value_expected = "/etc/sonic/telemetry/dsmsroot.cer"
            pytest_assert(str(value) == ca_crt_value_expected, "'ca_crt' value is not '{}'".format(ca_crt_value_expected))
        if str(key) == "server_key":
            server_key_expected = "/etc/sonic/telemetry/streamingtelemetryserver.key"
            pytest_assert(str(value) == server_key_expected, "'server_key' value is not '{}'".format(server_key_expected))
        if str(key) == "server_crt":
            server_crt_expected = "/etc/sonic/telemetry/streamingtelemetryserver.cer"
            pytest_assert(str(value) == server_crt_expected, "'server_crt' value is not '{}'".format(server_crt_expected))

def test_telemetry_enabledbydefault(duthost):
    """Verify telemetry should be enabled by default
    """
    docker_present = verify_telemetry_dockerimage(duthost)
    if not docker_present:
        pytest.skip("docker-sonic-telemetry is not part of the image")

    status = duthost.shell('sonic-db-cli CONFIG_DB HGETALL "FEATURE|telemetry"', module_ignore_errors=False)['stdout_lines']
    status_list = get_list_stdout(status)
    # Elements in list alternate between key and value. Separate them and combine into a dict.
    status_key_list = status_list[0::2]
    status_value_list = status_list[1::2]
    status_dict = dict(zip(status_key_list, status_value_list))
    for k, v in status_dict.items():
        if str(k) == "status":
            status_expected = "enabled";
            pytest_assert(str(v) == status_expected, "Telemetry feature is not enabled")

def test_telemetry_ouput(duthost, ptfhost, localhost):
    """Run pyclient from ptfdocker and show gnmi server outputself.
    """
    docker_present = verify_telemetry_dockerimage(duthost)
    if not docker_present:
        pytest.skip("docker-sonic-telemetry is not part of the image")

    logger.info('start telemetry output testing')
    setup_telemetry_forpyclient(duthost)

    # wait till telemetry is restarted
    pytest_assert(wait_until(100, 10, duthost.is_service_fully_started, "telemetry"), "TELEMETRY not started")
    logger.info('telemetry process restarted. Now run pyclient on ptfdocker')

    # Wait until the TCP port is open
    dut_ip = duthost.mgmt_ip
    wait_tcp_connection(localhost, dut_ip, TELEMETRY_PORT, timeout_s=60)

    # pyclient should be available on ptfhost. If not fail pytest.
    file_exists = ptfhost.stat(path="/gnxi/gnmi_cli_py/py_gnmicli.py")
    pytest_assert(file_exists["stat"]["exists"] is True)
    cmd = 'python /gnxi/gnmi_cli_py/py_gnmicli.py -g -t {0} -p {1} -m get -x COUNTERS/Ethernet0 -xt COUNTERS_DB \
           -o "ndastreamingservertest"'.format(dut_ip, TELEMETRY_PORT)
    show_gnmi_out = ptfhost.shell(cmd)['stdout']
    logger.info("GNMI Server output")
    logger.info(show_gnmi_out)
    result = str(show_gnmi_out)
    inerrors_match = re.search("SAI_PORT_STAT_IF_IN_ERRORS", result)
    pytest_assert(inerrors_match is not None, "SAI_PORT_STAT_IF_IN_ERRORS not found in gnmi_output")

def test_osbuild_version(duthost, ptfhost, localhost):
    """ Test osbuild/version query.
    """

    cmd = generate_client_cli(duthost=duthost, mode=mode_get, target="OTHERS", xpath="osversion/build")
    logger.info("Command to run: {0}".format(cmd))
    show_gnmi_out = ptfhost.shell(cmd)['stdout']
    logger.info(show_gnmi_out)
    result = str(show_gnmi_out)

    assert_equal(len(re.findall('"build_version": "sonic', result)), 1, "build_version value")
