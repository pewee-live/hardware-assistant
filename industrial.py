"""Industrial protocol clients: SNMP, Modbus, Redfish, IPMI.

Each protocol is wrapped in a small client class that takes connection params,
performs queries, and returns human-readable results. The classes are designed
for "query on demand" rather than persistent connections (except Modbus, which
holds a TCP/RTU link open for the session).

These are exposed to the agent as tools via tools.py so the agent can inspect
network gear, PLCs, and server BMCs that have no shell access at all.
"""
from typing import Optional, Any
import json
import time


# ---------------------------------------------------------------------------
# SNMP -- network devices (switches, routers, APs, printers, PDUs, UPSes).
# ---------------------------------------------------------------------------

# A curated map of human-readable names -> SNMP OIDs, so the agent can ask for
# "sysDescr" or "ifInOctets" without memorizing dotted strings. These cover the
# most common RFC1213 / IF-MIB / HOST-RESOURCES values.
SNMP_OID_MAP = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysContact": "1.3.6.1.2.1.1.4.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
    "sysLocation": "1.3.6.1.2.1.1.6.0",
    "ifNumber": "1.3.6.1.2.1.2.1.0",
    "hrSystemUptime": "1.3.6.1.2.1.25.1.1.0",
    "hrMemorySize": "1.3.6.1.2.1.25.2.2.0",
    # Interface table (walked, not scalar):
    "ifTable": "1.3.6.1.2.1.2.2",
    "ifDescr": "1.3.6.1.2.1.2.2.1.2",
    "ifOperStatus": "1.3.6.1.2.1.2.2.1.8",
    "ifInOctets": "1.3.6.1.2.1.2.2.1.10",
    "ifOutOctets": "1.3.6.1.2.1.2.2.1.16",
}


class SnmpClient:
    """Query a network device via SNMP v2c get/walk, implemented over raw UDP
    with pyasn1 BER encoding. No SNMP library dependency -- this avoids the
    version churn of pysnmp 4/6/7 whose hlapi API breaks between releases."""

    def __init__(self, host: str, community: str = "public", port: int = 161, version: int = 2):
        self.host = host
        self.community = community
        self.port = port
        self.version = version
        self._req_id = 1

    def _resolve_oid(self, name_or_oid: str) -> str:
        return SNMP_OID_MAP.get(name_or_oid.strip(), name_or_oid.strip())

    def _encode_oid(self, oid_str: str) -> bytes:
        """Minimal BER OID encoder for a dotted-string OID."""
        parts = [int(x) for x in oid_str.split('.')]
        if len(parts) < 2:
            parts = [0, 0] + parts
        first = parts[0] * 40 + parts[1]
        out = bytearray([first])
        for p in parts[2:]:
            if p < 0x80:
                out.append(p)
            else:
                stack = []
                stack.append(p & 0x7F)
                p >>= 7
                while p > 0:
                    stack.append((p & 0x7F) | 0x80)
                    p >>= 7
                out.extend(reversed(stack))
        return bytes(out)

    def _ber_len(self, n: int) -> bytes:
        if n < 0x80:
            return bytes([n])
        out = []
        while n > 0:
            out.insert(0, n & 0xFF)
            n >>= 8
        return bytes([0x80 | len(out)]) + bytes(out)

    def _ber_tlv(self, tag: int, value: bytes) -> bytes:
        return bytes([tag]) + self._ber_len(len(value)) + value

    def _build_get_request(self, oid_str: str, pdu_tag: int) -> bytes:
        from pyasn1.codec.ber import encoder
        # Encode the community string and version (1 = v2c).
        version_bytes = self._ber_tlv(0x02, b'\x01')  # INTEGER 1 = SNMPv2c
        community_bytes = self._ber_tlv(0x04, self.community.encode('ascii'))
        # OID as raw ObjectIdentifier value bytes (we encode it ourselves for
        # reliability across pyasn1 minor versions).
        oid_val = self._encode_oid(oid_str)
        oid_tlv = self._ber_tlv(0x06, oid_val)
        # varbind: SEQUENCE { OID, NULL }
        null_tlv = self._ber_tlv(0x05, b'')
        varbind = self._ber_tlv(0x30, oid_tlv + null_tlv)
        # varbind list
        vbl = self._ber_tlv(0x30, varbind)
        # PDU: GetRequest [0] or GetNextRequest [1]
        req_id = self._ber_tlv(0x02, self._req_id.to_bytes(1, 'big') if self._req_id < 256 else self._req_id.to_bytes((self._req_id.bit_length()+7)//8, 'big'))
        err_stat = self._ber_tlv(0x02, b'\x00')
        err_idx = self._ber_tlv(0x02, b'\x00')
        pdu_body = req_id + err_stat + err_idx + vbl
        pdu = self._ber_tlv(pdu_tag, pdu_body)
        # Message: SEQUENCE { version, community, pdu }
        msg = self._ber_tlv(0x30, version_bytes + community_bytes + pdu)
        self._req_id += 1
        return bytes(msg)

    def _decode_response(self, data: bytes) -> str:
        """Parse a minimal SNMPv2c response, extracting varbind values."""
        from pyasn1.codec.ber import decoder as ber_decoder
        try:
            msg, _ = ber_decoder.decode(data)
            # msg = SEQUENCE { version, community, pdu }
            pdu = msg[2]
            # pdu = GetResponse [2] { reqid, error-status, error-index, varbinds }
            error_status = int(pdu[1])
            error_index = int(pdu[2])
            if error_status:
                return f"SNMP error: status {error_status} at index {error_index}"
            varbinds = pdu[3]
            results = []
            for vb in varbinds:
                oid = '.'.join(str(x) for x in vb[0])
                val = vb[1]
                # Render the value as best-effort text.
                results.append(f"{oid} = {val}")
            return '\n'.join(results) if results else 'No data returned.'
        except Exception as e:
            return f"SNMP decode error: {e}"

    def _send_recv(self, payload: bytes) -> bytes:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        try:
            sock.sendto(payload, (self.host, self.port))
            data, _ = sock.recvfrom(65535)
            return data
        finally:
            sock.close()

    def get(self, oid_name: str) -> str:
        """SNMP GET for a single scalar OID."""
        oid = self._resolve_oid(oid_name)
        try:
            req = self._build_get_request(oid, 0xA0)  # GetRequest context tag [0]
            resp = self._send_recv(req)
            return self._decode_response(resp)
        except Exception as e:
            return f"SNMP error: {e}"

    def walk(self, oid_name: str, max_repeats: int = 50) -> str:
        """SNMP WALK via repeated GetNext, stopping when the OID subtree ends."""
        oid = self._resolve_oid(oid_name)
        results = []
        current_oid = oid
        try:
            for _ in range(max_repeats):
                req = self._build_get_request(current_oid, 0xA1)  # GetNextRequest [1]
                resp = self._send_recv(req)
                line = self._decode_response(resp)
                if line.startswith('SNMP error') or line.startswith('SNMP decode'):
                    if results:
                        break  # End of subtree is sometimes reported as error
                    return line
                # Extract the returned OID to check if we left the subtree.
                returned_oid = line.split(' = ')[0].strip() if ' = ' in line else ''
                if not returned_oid.startswith(oid):
                    break
                results.append(line)
                current_oid = returned_oid
            return '\n'.join(results) if results else 'No data returned.'
        except Exception as e:
            return f"SNMP error: {e}"



# ---------------------------------------------------------------------------
# Modbus -- industrial PLCs, sensors, energy meters.
# ---------------------------------------------------------------------------

class ModbusClient:
    """Read/write holding registers and coils on a Modbus TCP device.

    The TCP connection is opened lazily on first use and reused for subsequent
    operations within the same session.
    """

    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self._client = None

    def _connect(self):
        if self._client is None:
            from pymodbus.client import ModbusTcpClient
            self._client = ModbusTcpClient(
                self.host, port=self.port, timeout=self.timeout,
            )
        if not self._client.connect():
            raise ConnectionError(f"Could not connect to Modbus TCP {self.host}:{self.port}")

    def read_holding_registers(self, address: int, count: int = 1) -> str:
        self._connect()
        rr = self._client.read_holding_registers(address, count, slave=self.unit_id)
        if rr.isError():
            return f"Modbus error: {rr}"
        vals = rr.registers
        return f"Registers at {address} (count {count}): {vals}"

    def read_coils(self, address: int, count: int = 1) -> str:
        self._connect()
        rr = self._client.read_coils(address, count, slave=self.unit_id)
        if rr.isError():
            return f"Modbus error: {rr}"
        vals = rr.bits[:count]
        return f"Coils at {address} (count {count}): {vals}"

    def write_register(self, address: int, value: int) -> str:
        self._connect()
        rq = self._client.write_register(address, value, slave=self.unit_id)
        if rq.isError():
            return f"Modbus error: {rq}"
        return f"Written value {value} to register {address}."

    def write_coil(self, address: int, value: bool) -> str:
        self._connect()
        rq = self._client.write_coil(address, value, slave=self.unit_id)
        if rq.isError():
            return f"Modbus error: {rq}"
        return f"Written value {value} to coil {address}."

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


# ---------------------------------------------------------------------------
# Redfish -- modern server BMC out-of-band management (DMTF REST API).
# ---------------------------------------------------------------------------

class RedfishClient:
    """Query a server BMC via the Redfish REST API.

    Redfish uses HTTPS with Basic auth. Common roots: /redfish/v1/Systems,
    /redfish/v1/Chassis, /redfish/v1/Managers. The agent supplies a path; we
    return the JSON response (truncated if very large).
    """

    def __init__(self, host: str, username: str, password: str, port: int = 443,
                 use_https: bool = True, verify_ssl: bool = False):
        scheme = "https" if use_https else "http"
        self.base_url = f"{scheme}://{host}:{port}"
        self.auth = (username, password)
        self.verify_ssl = verify_ssl
        self._session = None

    def _get_session(self):
        if self._session is None:
            import httpx
            self._session = httpx.Client(
                auth=self.auth, verify=self.verify_ssl, timeout=10.0,
                headers={"Accept": "application/json", "OData-Version": "4.0"},
            )
        return self._session

    def _path(self, path: str) -> str:
        if path.startswith("/redfish"):
            return self.base_url + path
        return self.base_url + "/redfish/v1/" + path.lstrip("/")

    def get(self, path: str) -> str:
        """GET a Redfish resource path (e.g. 'Systems', 'Chassis/1/Thermal')."""
        s = self._get_session()
        url = self._path(path)
        try:
            r = s.get(url)
        except Exception as e:
            return f"Redfish request error: {e}"
        if r.status_code != 200:
            return f"Redfish HTTP {r.status_code}: {r.text[:500]}"
        try:
            data = r.json()
        except Exception:
            return r.text[:2000]
        text = json.dumps(data, indent=2, ensure_ascii=False)
        if len(text) > 6000:
            text = text[:3000] + f"\n... [truncated {len(text)-6000} chars] ...\n" + text[-3000:]
        return text

    def root(self) -> str:
        """Return the Redfish service root for discovery."""
        return self.get("/redfish/v1/")

    def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None


# ---------------------------------------------------------------------------
# IPMI -- traditional server BMC out-of-band management (raw IPMI v2.0).
# ---------------------------------------------------------------------------

class IpmiClient:
    """Query a server BMC via IPMI 2.0 (RMCP+ LAN).

    Uses pyghmi (Python General Hardware Management Interface) which speaks
    IPMI over LAN. Covers power state, sensors, SEL (system event log), and
    basic chassis identity.
    """

    def __init__(self, host: str, username: str, password: str, port: int = 623):
        self.host = host
        self.username = username
        self.password = password
        self.port = port

    def _connect(self):
        from pyghmi.ipmi import command
        return command.Command(
            bmc=self.host, userid=self.username, password=self.password,
            port=self.port,
        )

    def get_power_state(self) -> str:
        try:
            conn = self._connect()
            state = conn.get_power()
            return f"Power state: {state}"
        except Exception as e:
            return f"IPMI error: {e}"

    def get_sensors(self) -> str:
        try:
            conn = self._connect()
            data = conn.get_sensor_data()
            lines = []
            for s in data:
                # pyghmi sensor objects expose name, value, units, states, etc.
                name = getattr(s, "name", "?")
                value = getattr(s, "value", "?")
                units = getattr(s, "units", "")
                states = getattr(s, "states", None)
                line = f"{name}: {value} {units}".strip()
                if states:
                    line += f"  [{', '.join(str(x) for x in states if x)}]"
                lines.append(line)
            return "\n".join(lines) if lines else "No sensor data returned."
        except Exception as e:
            return f"IPMI error: {e}"

    def get_sel(self) -> str:
        """Read the System Event Log."""
        try:
            conn = self._connect()
            sel = conn.get_sel_json()
            if not sel:
                return "SEL is empty."
            lines = []
            for entry in sel:
                ts = entry.get("date", "?")
                desc = entry.get("desc", str(entry))
                lines.append(f"[{ts}] {desc}")
            return "\n".join(lines)
        except Exception as e:
            return f"IPMI error: {e}"

    def get_identify(self) -> str:
        try:
            conn = self._connect()
            info = conn.get_inventory()
            text = json.dumps(info, indent=2, ensure_ascii=False, default=str) if info else "{}"
            if len(text) > 6000:
                text = text[:3000] + f"\n... [truncated] ...\n" + text[-3000:]
            return text
        except Exception as e:
            return f"IPMI error: {e}"