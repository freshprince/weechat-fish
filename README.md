FiSH for weechat
================

This is a python plugin for weechat.  It implements blowfish encryption and
DH1080 key exchange and should be compatible with FiSH from
http://fish.secure.la/

v0.9
----
Can use [weechat-secure][weechat secured data] to store keys. To encrypt keys:
```
/secure set fish *********
/set fish.secure.key "${sec.data.fish}"
```

Or you can set a randomly generated key with:
```
/blowkey genkey
```

To return to storing in plain text:
```
/sec fish.secure.key ""
```

[weechat-secure]: http://dev.weechat.org/post/2013/08/04/Secured-data
