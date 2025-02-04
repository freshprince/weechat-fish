# -*- coding: utf-8 -*-
#
# Copyright (C) David Flatz <david@upcs.at>
# Copyright (C) 2017 Ricardo Ferreira <ricardo.sff@goatse.cx>
# Copyright (C) 2012 Markus Näsman <markus@botten.org>
# Copyright (C) 2009 Bjorn Edstrom <be@bjrn.se>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

#
# NOTE: Blowfish and DH1080 implementation is licenced under a different
# license:
#
# Copyright (c) 2009, Bjorn Edstrom <be@bjrn.se>
#
# Permission to use, copy, modify, and distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#

#
# Suggestions, Bugs, ...?
# https://github.com/freshprince/weechat-fish

#
# NOTE ABOUT DH1080:
# =================
#
# Diffie-Hellman key exchange assumes that you already have
# authenticated channels between Alice and Bob.  Which means that Alice
# has to be sure that she is really talking to Bob and not to any man in
# the middle.  But since the whole idea of FiSH is that you want to
# encrypt your communication on the IRC server whose operators you do
# not trust, there is no reliable way for Alice to tell if she really is
# talking to Bob.  It could also be some rogue IRC admin impersonating
# Bob with a fake hostname and ident or even doing a MITM attack on
# DH1080.  This means you can consider using DH1080 key exchange over
# IRC utterly broken in terms of security.
#

import re
import struct
import hashlib
import base64
import sys
from os import urandom

SCRIPT_NAME = "fish"
SCRIPT_AUTHOR = "David Flatz <david@upcs.at>"
SCRIPT_VERSION = "0.14"
SCRIPT_LICENSE = "GPL3"
SCRIPT_DESC = "FiSH for weechat"
CONFIG_FILE_NAME = SCRIPT_NAME

import_ok = True

try:
    import weechat
except ImportError:
    print("This script must be run under WeeChat.")
    print("Get WeeChat now at: http://www.weechat.org/")
    import_ok = False

try:
    import Crypto.Cipher.Blowfish as CryptoBlowfish
except ImportError:
    try:
        import Cryptodome.Cipher.Blowfish as CryptoBlowfish
    except ImportError:
        print("Pycryptodome must be installed to use fish")
        import_ok = False


#
# GLOBALS
#

fish_config_file = None
fish_config_section = {}
fish_config_option = {}
fish_keys = {}
fish_cbc = {}
fish_cyphers = {}
fish_DH1080ctx = {}
fish_encryption_announced = {}


#
# CONFIG
#

def fish_config_reload_cb(data, config_file):
    return weechat.config_reload(config_file)


def fish_config_keys_read_cb(data, config_file, section_name, option_name,
                             value):
    global fish_keys, fish_cbc

    option = weechat.config_new_option(
            config_file, section_name, option_name, "string", "key", "", 0, 0,
            "", value, 0, "", "", "", "", "", "")
    if not option:
        return weechat.WEECHAT_CONFIG_OPTION_SET_ERROR

    if value.startswith('cbc:'):
        fish_cbc[option_name] = True
        value = value[4:]

    fish_keys[option_name] = value

    return weechat.WEECHAT_CONFIG_OPTION_SET_OK_CHANGED


def fish_config_keys_write_cb(data, config_file, section_name):
    global fish_keys, fish_cbc

    weechat.config_write_line(config_file, section_name, "")
    for target, key in sorted(fish_keys.items()):
        if fish_cbc.get(target, False):
            key = 'cbc:' + key
        weechat.config_write_line(config_file, target, key)

    return weechat.WEECHAT_RC_OK


def fish_config_init():
    global fish_config_file, fish_config_section, fish_config_option

    fish_config_file = weechat.config_new(
            CONFIG_FILE_NAME, "fish_config_reload_cb", "")
    if not fish_config_file:
        return

    # look
    fish_config_section["look"] = weechat.config_new_section(
            fish_config_file, "look", 0, 0, "", "", "", "", "", "", "", "", "",
            "")
    if not fish_config_section["look"]:
        weechat.config_free(fish_config_file)
        return

    fish_config_option["announce"] = weechat.config_new_option(
            fish_config_file, fish_config_section["look"], "announce",
            "boolean", "announce if messages are being encrypted or not", "",
            0, 0, "on", "on", 0, "", "", "", "", "", "")
    fish_config_option["marker"] = weechat.config_new_option(
            fish_config_file, fish_config_section["look"], "marker",
            "string", "marker for important FiSH messages", "", 0, 0,
            "O<", "O<", 0, "", "", "", "", "", "")

    # color
    fish_config_section["color"] = weechat.config_new_section(
            fish_config_file, "color", 0, 0, "", "", "", "", "", "", "", "",
            "", "")
    if not fish_config_section["color"]:
        weechat.config_free(fish_config_file)
        return

    fish_config_option["alert"] = weechat.config_new_option(
            fish_config_file, fish_config_section["color"], "alert",
            "color", "color for important FiSH message markers", "", 0, 0,
            "lightblue", "lightblue", 0, "", "", "", "", "", "")

    # keys
    fish_config_section["keys"] = weechat.config_new_section(
            fish_config_file, "keys", 0, 0, "fish_config_keys_read_cb", "",
            "fish_config_keys_write_cb", "", "", "", "", "", "", "")
    if not fish_config_section["keys"]:
        weechat.config_free(fish_config_file)
        return


def fish_config_read():
    global fish_config_file

    return weechat.config_read(fish_config_file)


def fish_config_write():
    global fish_config_file

    return weechat.config_write(fish_config_file)


##
# Blowfish and DH1080 Code:
##
#
# BLOWFISH
#

class Blowfish:

    def __init__(self, key=None):
        if key:
            if len(key) > 72:
                key = key[:72]
            self.blowfish = CryptoBlowfish.new(
                    key.encode('utf-8'), CryptoBlowfish.MODE_ECB)

    def decrypt(self, data):
        return self.blowfish.decrypt(data)

    def encrypt(self, data):
        return self.blowfish.encrypt(data)


# XXX: Unstable.
def blowcrypt_b64encode(s):
    """A non-standard base64-encode."""
    B64 = "./0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    res = ''
    while s:
        left, right = struct.unpack('>LL', s[:8])
        for i in range(6):
            res += B64[right & 0x3f]
            right >>= 6
        for i in range(6):
            res += B64[left & 0x3f]
            left >>= 6
        s = s[8:]
    return res


def blowcrypt_b64decode(s):
    """A non-standard base64-decode."""
    B64 = "./0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    res = []
    while s:
        left, right = 0, 0
        for i, p in enumerate(s[0:6]):
            right |= B64.index(p) << (i * 6)
        for i, p in enumerate(s[6:12]):
            left |= B64.index(p) << (i * 6)
        for i in range(0, 4):
            res.append((left & (0xFF << ((3 - i) * 8))) >> ((3 - i) * 8))
        for i in range(0, 4):
            res.append((right & (0xFF << ((3 - i) * 8))) >> ((3 - i) * 8))
        s = s[12:]
    return bytes(res)


def padto(msg, length):
    """Pads 'msg' with zeroes until it's length is divisible by 'length'.
    If the length of msg is already a multiple of 'length', does nothing."""
    L = len(msg)
    if L % length:
        msg += b'\x00' * (length - L % length)
    assert len(msg) % length == 0
    return msg


def blowcrypt_pack(msg, cipher, cbcKey=None):
    """."""
    if cbcKey is not None: # CBC mode
        cbcCipher = CryptoBlowfish.new(
                cbcKey.encode('utf-8'), CryptoBlowfish.MODE_CBC)
        return '+OK *'+ base64.b64encode(cbcCipher.iv + cbcCipher.encrypt(padto(msg, 8))).decode('utf-8')
    else:
        return '+OK ' + blowcrypt_b64encode(cipher.encrypt(padto(msg, 8)))


def blowcrypt_unpack(msg, cipher, key):
    """."""
    if not (msg.startswith('+OK ') or msg.startswith('mcps ')):
        raise ValueError
    _, rest = msg.split(' ', 1)

    if rest.startswith('*'):  # CBC mode
        rest = rest[1:]
        if len(rest) % 4:
            rest += '=' * (4 - len(rest) % 4)
        raw = base64.b64decode(rest)

        iv = raw[:8]
        raw = raw[8:]

        cbcCipher = CryptoBlowfish.new(
                key.encode('utf-8'), CryptoBlowfish.MODE_CBC, iv)

        plain = cbcCipher.decrypt(padto(raw, 8))

    else:

        if len(rest) < 12:
            raise ValueError

        if not (len(rest) % 12) == 0:
            rest = rest[:-(len(rest) % 12)]

        try:
            raw = blowcrypt_b64decode(padto(rest, 12))
        except TypeError:
            raise ValueError
        if not raw:
            raise ValueError

        plain = cipher.decrypt(raw)

    return plain.strip(b'\x00').replace(b'\n', b'')


#
# DH1080
#

g_dh1080 = 2
p_dh1080 = int('FBE1022E23D213E8ACFA9AE8B9DFAD'
               'A3EA6B7AC7A7B7E95AB5EB2DF85892'
               '1FEADE95E6AC7BE7DE6ADBAB8A783E'
               '7AF7A7FA6A2B7BEB1E72EAE2B72F9F'
               'A2BFB2A2EFBEFAC868BADB3E828FA8'
               'BADFADA3E4CC1BE7E8AFE85E9698A7'
               '83EB68FA07A77AB6AD7BEB618ACF9C'
               'A2897EB28A6189EFA07AB99A8A7FA9'
               'AE299EFA7BA66DEAFEFBEFBF0B7D8B', 16)
q_dh1080 = (p_dh1080 - 1) // 2


def dh1080_b64encode(s):
    """A non-standard base64-encode."""
    b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    d = [0] * len(s) * 2

    L = len(s) * 8
    m = 0x80
    i, j, k, t = 0, 0, 0, 0
    while i < L:
        if s[i >> 3] & m:
            t |= 1
        j += 1
        m >>= 1
        if not m:
            m = 0x80
        if not j % 6:
            d[k] = b64[t]
            t &= 0
            k += 1
        t <<= 1
        t %= 0x100
        #
        i += 1
    m = 5 - j % 6
    t <<= m
    t %= 0x100
    if m:
        d[k] = b64[t]
        k += 1
    d[k] = 0
    res = ''
    for q in d:
        if q == 0:
            break
        res += q
    return res


def dh1080_b64decode(s):
    """A non-standard base64-encode."""
    b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    buf = [0] * 256
    for i in range(64):
        buf[ord(b64[i])] = i

    L = len(s)
    if L < 2:
        raise ValueError
    for i in reversed(list(range(L - 1))):
        if buf[ord(s[i])] == 0:
            L -= 1
        else:
            break
    if L < 2:
        raise ValueError

    d = [0] * L
    i, k = 0, 0
    while True:
        i += 1
        if k + 1 < L:
            d[i - 1] = buf[ord(s[k])] << 2
            d[i - 1] %= 0x100
        else:
            break
        k += 1
        if k < L:
            d[i - 1] |= buf[ord(s[k])] >> 4
        else:
            break
        i += 1
        if k + 1 < L:
            d[i - 1] = buf[ord(s[k])] << 4
            d[i - 1] %= 0x100
        else:
            break
        k += 1
        if k < L:
            d[i - 1] |= buf[ord(s[k])] >> 2
        else:
            break
        i += 1
        if k + 1 < L:
            d[i - 1] = buf[ord(s[k])] << 6
            d[i - 1] %= 0x100
        else:
            break
        k += 1
        if k < L:
            d[i - 1] |= buf[ord(s[k])] % 0x100
        else:
            break
        k += 1
    return bytes(d[0:i - 1])


def dh_validate_public(public, q, p):
    """See RFC 2631 section 2.1.5."""
    return 1 == pow(public, q, p)


class DH1080Ctx:
    """DH1080 context."""
    def __init__(self, cbc=True):
        self.public = 0
        self.private = 0
        self.secret = 0
        self.state = 0
        self.cbc = cbc

        bits = 1080
        while True:
            self.private = bytes2int(urandom(bits // 8))
            self.public = pow(g_dh1080, self.private, p_dh1080)
            if 2 <= self.public <= p_dh1080 - 1 and \
               dh_validate_public(self.public, q_dh1080, p_dh1080) == 1:
                break


def dh1080_pack(ctx):
    """."""
    if ctx.state == 0:
        ctx.state = 1
        cmd = "DH1080_INIT "
    else:
        cmd = "DH1080_FINISH "
    return cmd + dh1080_b64encode(int2bytes(ctx.public)) + (" CBC" if ctx.cbc else "")


def dh1080_unpack(msg, ctx):
    """."""
    if not msg.startswith("DH1080_"):
        raise ValueError

    if ctx.state == 0:
        if not msg.startswith("DH1080_INIT ") and not msg.startswith("DH1080_INIT_CBC "):
            raise ValueError
        ctx.state = 1
        try:
            cmd, public_raw, *rest = msg.split(' ')
            public = bytes2int(dh1080_b64decode(public_raw))

            if not 1 < public < p_dh1080:
                raise ValueError

            ctx.secret = pow(public, ctx.private, p_dh1080)
            ctx.cbc = "CBC" in rest or cmd == "DH1080_INIT_CBC"

        except Exception:
            raise ValueError

    elif ctx.state == 1:
        if not msg.startswith("DH1080_FINISH "):
            raise ValueError
        ctx.state = 1
        try:
            cmd, public_raw, *rest = msg.split(' ')
            public = bytes2int(dh1080_b64decode(public_raw))

            if not 1 < public < p_dh1080:
                raise ValueError

            ctx.secret = pow(public, ctx.private, p_dh1080)
            ctx.cbc = "CBC" in rest

        except Exception:
            raise ValueError

    return True


def dh1080_secret(ctx):
    """."""
    if ctx.secret == 0:
        raise ValueError
    return dh1080_b64encode(sha256(int2bytes(ctx.secret)))


def bytes2int(b):
    """Variable length big endian to integer."""
    n = 0
    for p in b:
        n *= 256
        n += p
    return n


def int2bytes(n):
    """Integer to variable length big endian."""
    if n == 0:
        return b'\x00'
    b = []
    while n:
        b.insert(0, n % 256)
        n //= 256
    return bytes(b)


def sha256(s):
    """sha256"""
    return hashlib.sha256(s).digest()


##
#  END Blowfish and DH1080 Code
##
#
# HOOKS
#

def fish_modifier_in_notice_cb(data, modifier, server_name, string):
    global fish_DH1080ctx, fish_keys, fish_cyphers, fish_cbc

    if type(string) is bytes:
        return string

    match = re.match(
        r"^((?:@[^ ]* )?:(.*?)!.*? NOTICE (.*?) :)"
        r"((DH1080_INIT |DH1080_INIT_CBC |DH1080_FINISH |\+OK |mcps )?.*)$",
        string)
    # match.group(0): message
    # match.group(1): msg without payload
    # match.group(2): source
    # match.group(3): target
    # match.group(4): msg
    # match.group(5): "DH1080_INIT "|"DH1080_INIT_CBC "|"DH1080_FINISH "|"+OK "|"mcps "
    if not match or not match.group(5):
        return string

    if match.group(3) != weechat.info_get("irc_nick", server_name):
        return string

    target = "%s/%s" % (server_name, match.group(2))
    targetl = ("%s/%s" % (server_name, match.group(2))).lower()
    buffer = weechat.info_get("irc_buffer", "%s,%s" % (
            server_name, match.group(2)))

    if match.group(5) == "DH1080_FINISH " and targetl in fish_DH1080ctx:
        if not dh1080_unpack(match.group(4), fish_DH1080ctx[targetl]):
            fish_announce_unencrypted(buffer, target)
            return string

        fish_alert(buffer, "Key exchange for %s successful" % target)

        fish_keys[targetl] = dh1080_secret(fish_DH1080ctx[targetl])
        fish_cbc[targetl] = fish_DH1080ctx[targetl].cbc
        if targetl in fish_cyphers:
            del fish_cyphers[targetl]
        del fish_DH1080ctx[targetl]

        return ""

    if match.group(5) == "DH1080_INIT " or match.group(5) == "DH_1080_INIT_CBC ":
        fish_DH1080ctx[targetl] = DH1080Ctx()

        if not dh1080_unpack(match.group(4), fish_DH1080ctx[targetl]):
            fish_announce_unencrypted(buffer, target)
            return string

        reply = dh1080_pack(fish_DH1080ctx[targetl])

        fish_alert(buffer, "Key exchange initiated by %s. Key set." % target)

        weechat.command(buffer, "/mute notice -server %s %s %s" % (
                server_name, match.group(2), reply))

        fish_keys[targetl] = dh1080_secret(fish_DH1080ctx[targetl])
        fish_cbc[targetl] = fish_DH1080ctx[targetl].cbc
        if targetl in fish_cyphers:
            del fish_cyphers[targetl]
        del fish_DH1080ctx[targetl]

        return ""

    if match.group(5) in ["+OK ", "mcps "]:
        if targetl not in fish_keys:
            fish_announce_unencrypted(buffer, target)
            return string

        key = weechat.string_eval_expression(fish_keys[targetl], {}, {},{})

        try:
            if targetl not in fish_cyphers:
                b = Blowfish(key)
                fish_cyphers[targetl] = b
            else:
                b = fish_cyphers[targetl]

            clean = blowcrypt_unpack(match.group(4), b, key)

            fish_announce_encrypted(buffer, target)

            return b"%s%s" % (match.group(1).encode(), clean)
        except Exception as e:
            fish_announce_unencrypted(buffer, target)

            raise e

    fish_announce_unencrypted(buffer, target)

    return string


def fish_modifier_in_privmsg_cb(data, modifier, server_name, string):
    global fish_keys, fish_cyphers

    if type(string) is bytes:
        return string

    match = re.match(
        r"^((?:@[^ ]* )?:(.*?)!.*? PRIVMSG (.*?) :)(\x01ACTION )?"
        r"((\+OK |mcps )?.*?)(\x01)?$",
        string)
    # match.group(0): message
    # match.group(1): msg without payload
    # match.group(2): source
    # match.group(3): target
    # match.group(4): action
    # match.group(5): msg
    # match.group(6): "+OK "|"mcps "
    if not match:
        return string

    if match.group(3) == weechat.info_get("irc_nick", server_name):
        dest = match.group(2)
    else:
        dest = match.group(3)
    target = "%s/%s" % (server_name, dest)
    targetl = ("%s/%s" % (server_name, dest)).lower()
    buffer = weechat.info_get("irc_buffer", "%s,%s" % (server_name, dest))

    if not match.group(6):
        fish_announce_unencrypted(buffer, target)

        return string

    if targetl not in fish_keys:
        fish_announce_unencrypted(buffer, target)

        return string

    key = weechat.string_eval_expression(fish_keys[targetl], {}, {}, {})

    try:
        if targetl not in fish_cyphers:
            b = Blowfish(key)
            fish_cyphers[targetl] = b
        else:
            b = fish_cyphers[targetl]

        clean = blowcrypt_unpack(match.group(5), b, key)

        fish_announce_encrypted(buffer, target)

        if not match.group(4):
            return b'%s%s' % (match.group(1).encode(), clean)

        return b"%s%s%s\x01" % (
                match.group(1).encode(), match.group(4).encode(), clean)

    except Exception as e:
        fish_announce_unencrypted(buffer, target)

        raise e


def fish_modifier_in_topic_cb(data, modifier, server_name, string):
    global fish_keys, fish_cyphers

    if type(string) is bytes:
        return string

    match = re.match(r"^((?:@[^ ]* )?:.*?!.*? TOPIC (.*?) :)((\+OK |mcps )?.*)$", string)
    # match.group(0): message
    # match.group(1): msg without payload
    # match.group(2): channel
    # match.group(3): topic
    # match.group(4): "+OK "|"mcps "
    if not match:
        return string

    target = "%s/%s" % (server_name, match.group(2))
    targetl = ("%s/%s" % (server_name, match.group(2))).lower()
    buffer = weechat.info_get("irc_buffer", "%s,%s" % (
        server_name, match.group(2)))

    if targetl not in fish_keys or not match.group(4):
        fish_announce_unencrypted(buffer, target)

        return string

    key = weechat.string_eval_expression(fish_keys[targetl], {}, {}, {})

    try:
        if targetl not in fish_cyphers:
            b = Blowfish(key)
            fish_cyphers[targetl] = b
        else:
            b = fish_cyphers[targetl]

        clean = blowcrypt_unpack(match.group(3), b, key)

        fish_announce_encrypted(buffer, target)

        return b"%s%s" % (match.group(1).encode(), clean)
    except Exception as e:
        fish_announce_unencrypted(buffer, target)

        raise e


def fish_modifier_in_332_cb(data, modifier, server_name, string):
    global fish_keys, fish_cyphers

    if type(string) is bytes:
        return string

    match = re.match(r"^((?:@[^ ]* )?:.*? 332 .*? (.*?) :)((\+OK |mcps )?.*)$", string)
    if not match:
        return string

    target = "%s/%s" % (server_name, match.group(2))
    targetl = ("%s/%s" % (server_name, match.group(2))).lower()
    buffer = weechat.info_get("irc_buffer", "%s,%s" % (
        server_name, match.group(2)))

    if targetl not in fish_keys or not match.group(4):
        fish_announce_unencrypted(buffer, target)

        return string

    key = weechat.string_eval_expression(fish_keys[targetl], {}, {}, {})

    try:
        if targetl not in fish_cyphers:
            b = Blowfish(key)
            fish_cyphers[targetl] = b
        else:
            b = fish_cyphers[targetl]

        clean = blowcrypt_unpack(match.group(3), b, key)

        fish_announce_encrypted(buffer, target)

        return b"%s%s" % (match.group(1).encode(), clean)
    except Exception as e:
        fish_announce_unencrypted(buffer, target)

        raise e


def fish_modifier_out_privmsg_cb(data, modifier, server_name, string):
    global fish_keys, fish_cyphers, fish_cbc

    if type(string) is bytes:
        return string

    match = re.match(r"^(PRIVMSG (.*?) :)(.*)$", string)
    if not match:
        return string

    target = "%s/%s" % (server_name, match.group(2))
    targetl = ("%s/%s" % (server_name, match.group(2))).lower()
    buffer = weechat.info_get("irc_buffer", "%s,%s" % (
        server_name, match.group(2)))

    if targetl not in fish_keys:
        fish_announce_unencrypted(buffer, target)

        return string

    if targetl not in fish_cyphers:
        b = Blowfish(weechat.string_eval_expression(fish_keys[targetl], {}, {}, {}))
        fish_cyphers[targetl] = b
    else:
        b = fish_cyphers[targetl]
    if fish_cbc.get(targetl, False):
        cbcKey = weechat.string_eval_expression(fish_keys[targetl], {}, {}, {})
    else:
        cbcKey = None
    cypher = blowcrypt_pack(match.group(3).encode(), b, cbcKey)

    fish_announce_encrypted(buffer, target)

    return "%s%s" % (match.group(1), cypher)


def fish_modifier_out_topic_cb(data, modifier, server_name, string):
    global fish_keys, fish_cyphers, fish_cbc

    if type(string) is bytes:
        return string

    match = re.match(r"^(TOPIC (.*?) :)(.*)$", string)
    if not match:
        return string
    if not match.group(3):
        return string

    target = "%s/%s" % (server_name, match.group(2))
    targetl = ("%s/%s" % (server_name, match.group(2))).lower()
    buffer = weechat.info_get("irc_buffer", "%s,%s" % (
        server_name, match.group(2)))

    if targetl not in fish_keys:
        fish_announce_unencrypted(buffer, target)

        return string

    if targetl not in fish_cyphers:
        b = Blowfish(weechat.string_eval_expression(fish_keys[targetl], {}, {}, {}))
        fish_cyphers[targetl] = b
    else:
        b = fish_cyphers[targetl]
    if fish_cbc.get(targetl, False):
        cbcKey = weechat.string_eval_expression(fish_keys[targetl], {}, {}, {})
    else:
        cbcKey = None
    cypher = blowcrypt_pack(match.group(3).encode(), b, cbcKey)

    fish_announce_encrypted(buffer, target)

    return "%s%s" % (match.group(1), cypher)


def fish_unload_cb():
    fish_config_write()

    return weechat.WEECHAT_RC_OK


#
# COMMANDS
#

def fish_cmd_blowkey(data, buffer, args):
    global fish_keys, fish_cyphers, fish_DH1080ctx, fish_cbc

    if args == "" or args == "list":
        fish_list_keys(buffer)

        return weechat.WEECHAT_RC_OK

    argv = args.split(" ")

    if (len(argv) > 2 and argv[1] == "-server"):
        server_name = argv[2]
        del argv[2]
        del argv[1]
        pos = args.find(" ")
        pos = args.find(" ", pos + 1)
        args = args[pos+1:]
    else:
        server_name = weechat.buffer_get_string(buffer, "localvar_server")

    buffer_type = weechat.buffer_get_string(buffer, "localvar_type")
    # if no target user has been specified grab the one from the buffer if it
    # is private
    if argv[0] == "exchange" and len(argv) == 1 and buffer_type == "private":
        target_user = weechat.buffer_get_string(buffer, "localvar_channel")
    elif (argv[0] == "set" and
            (buffer_type == "private" or buffer_type == "channel") and
            len(argv) == 2):
        target_user = weechat.buffer_get_string(buffer, "localvar_channel")
    elif len(argv) < 2:
        return weechat.WEECHAT_RC_ERROR
    else:
        target_user = argv[1]

    argv2eol = ""
    pos = args.find(" ")
    if pos:
        pos = args.find(" ", pos + 1)
        if pos > 0:
            argv2eol = args[pos + 1:]
        else:
            argv2eol = args[args.find(" ") + 1:]

    target = "%s/%s" % (server_name, target_user)
    targetl = ("%s/%s" % (server_name, target_user)).lower()

    if argv[0] == "set":
        if argv2eol.startswith('cbc:'):
            fish_cbc[targetl] = True
            argv2eol = argv2eol[4:]
        elif targetl in fish_cbc:
            del fish_cbc[targetl]
        fish_keys[targetl] = argv2eol

        if targetl in fish_cyphers:
            del fish_cyphers[targetl]

        if fish_cbc.get(targetl, False):
            cbcNote = " (cbc)"
        else:
            cbcNote = ""

        weechat.prnt(buffer, "set key for %s to %s%s" % (target, argv2eol, cbcNote))

        return weechat.WEECHAT_RC_OK

    if argv[0] == "remove":
        if not len(argv) == 2:
            return weechat.WEECHAT_RC_ERROR

        if targetl not in fish_keys:
            return weechat.WEECHAT_RC_ERROR

        del fish_keys[targetl]

        if targetl in fish_cyphers:
            del fish_cyphers[targetl]

        weechat.prnt(buffer, "removed key for %s" % target)

        return weechat.WEECHAT_RC_OK

    if argv[0] == "exchange":
        if server_name == "":
            return weechat.WEECHAT_RC_ERROR

        weechat.prnt(buffer, "Initiating DH1080 Exchange with %s" % target)
        fish_DH1080ctx[targetl] = DH1080Ctx()
        msg = dh1080_pack(fish_DH1080ctx[targetl])
        weechat.command(buffer, "/mute notice -server %s %s %s" % (
            server_name, target_user, msg))

        return weechat.WEECHAT_RC_OK

    return weechat.WEECHAT_RC_ERROR


#
# HELPERS
#

def fish_announce_encrypted(buffer, target):
    global fish_encryption_announced, fish_config_option

    if (not weechat.config_boolean(fish_config_option['announce']) or
            fish_encryption_announced.get(target)):
        return

    (server, nick) = target.split("/")

    if (weechat.info_get("irc_is_nick", nick) and
            weechat.buffer_get_string(buffer, "localvar_type") != "private"):
        # if we get a private message and there no buffer yet, create one and
        # jump back to the previous buffer
        weechat.command(buffer, "/mute query %s" % nick)
        buffer = weechat.info_get("irc_buffer", "%s,%s" % (server, nick))
        weechat.command(buffer, "/input jump_previously_visited_buffer")

    fish_alert(buffer, "Messages to/from %s are encrypted." % target)

    fish_encryption_announced[target] = True


def fish_announce_unencrypted(buffer, target):
    global fish_encryption_announced, fish_config_option

    if (not weechat.config_boolean(fish_config_option['announce']) or
            not fish_encryption_announced.get(target)):
        return

    fish_alert(buffer, "Messages to/from %s are %s*not*%s encrypted." % (
            target,
            weechat.color(weechat.config_color(fish_config_option["alert"])),
            weechat.color("chat")))

    del fish_encryption_announced[target]


def fish_alert(buffer, message):
    mark = "%s%s%s\t" % (
            weechat.color(weechat.config_color(fish_config_option["alert"])),
            weechat.config_string(fish_config_option["marker"]),
            weechat.color("chat"))

    weechat.prnt(buffer, "%s%s" % (mark, message))


def fish_list_keys(buffer):
    global fish_keys, fish_cbc

    weechat.prnt(buffer, "\tFiSH Keys: form target(server): key")

    for (target, key) in sorted(fish_keys.items()):
        (server, nick) = target.split("/")
        if fish_cbc.get(target, False):
            key = 'cbc:' + key
        weechat.prnt(buffer, "\t%s(%s): %s" % (nick, server, key))


#
# MAIN
#

if (__name__ == "__main__" and import_ok and
        weechat.register(
            SCRIPT_NAME, SCRIPT_AUTHOR, SCRIPT_VERSION, SCRIPT_LICENSE,
            SCRIPT_DESC, "fish_unload_cb", "")):

    weechat.hook_command(
            "blowkey", "Manage FiSH keys",
            "[list] | set [-server <server>] [<target>] <key> "
            "| remove [-server <server>] <target> "
            "| exchange [-server <server>] [<nick>]",
            "Add, change or remove key for target or perform DH1080 key"
            "exchange with <nick>.\n"
            "Target can be a channel or a nick.\n"
            "\n"
            "Without arguments this command lists all keys.\n"
            "\n"
            "Examples:\n"
            "Set the key for a channel: /blowkey set -server freenet #blowfish"
            " key\n"
            "Remove the key:            /blowkey remove #blowfish\n"
            "Set the key for a query:   /blowkey set nick secret+key\n"
            "List all keys:             /blowkey\n"
            "DH1080:                    /blowkey exchange nick\n"
            "\nPlease read the source for a note about DH1080 key exchange\n",
            "list || set %(irc_channel)|%(nicks)|-server %(irc_servers) %- "
            "|| remove %(irc_channel)|%(nicks)|-server %(irc_servers) %- "
            "|| exchange %(nick)|-server %(irc_servers) %-",
            "fish_cmd_blowkey", "")

    fish_config_init()
    fish_config_read()

    weechat.hook_modifier("irc_in_notice", "fish_modifier_in_notice_cb", "")
    weechat.hook_modifier("irc_in_privmsg", "fish_modifier_in_privmsg_cb", "")
    weechat.hook_modifier("irc_in_topic", "fish_modifier_in_topic_cb", "")
    weechat.hook_modifier("irc_in_332", "fish_modifier_in_332_cb", "")
    weechat.hook_modifier(
            "irc_out_privmsg", "fish_modifier_out_privmsg_cb", "")
    weechat.hook_modifier("irc_out_topic", "fish_modifier_out_topic_cb", "")
elif (__name__ == "__main__" and len(sys.argv) == 3):
    key = sys.argv[1]
    msg = sys.argv[2]
    print(blowcrypt_unpack(msg, Blowfish(key), key))
