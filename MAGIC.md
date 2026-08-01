# 魔法表情·单字符族 `<$X>`

**规律：`<$X>` 渲染的就是 face ID 为 X 的表情**（`滑稽`=`<$²>`，²=U+00B2=178），`/magic 178` 与 face ID 178 等价。

下表枚举码点 `1 ~ 0x7F`。复制「模板」列（含不可见控制字符）到 QQ 发送验证，
能渲染成表情的即为有效，无效只会显示乱码文本。

| ID | 十六进制 | 说明 | 模板 | 验证 |
| --- | --- | --- | --- | --- |
| 1 | 0x01 | U+0001 | `<$>` | |
| 2 | 0x02 | U+0002 | `<$>` | |
| 3 | 0x03 | U+0003 | `<$>` | |
| 4 | 0x04 | U+0004 | `<$>` | |
| 5 | 0x05 | U+0005 | `<$>` | |
| 6 | 0x06 | U+0006 | `<$>` | |
| 7 | 0x07 | U+0007 | `<$>` | |
| 8 | 0x08 | U+0008 | `<$>` | |
| 9 | 0x09 | 制表符 (TAB) | `<$	>` | |
| 10 | 0x0A | 换行符 (LF) | `<$\n>` | |
| 11 | 0x0B | U+000B | `<$>` | |
| 12 | 0x0C | U+000C | `<$>` | |
| 13 | 0x0D | 回车 (CR) | `<$\r>` | |
| 14 | 0x0E | U+000E | `<$>` | |
| 15 | 0x0F | U+000F | `<$>` | |
| 16 | 0x10 | U+0010 | `<$>` | |
| 17 | 0x11 | U+0011 | `<$>` | |
| 18 | 0x12 | U+0012 | `<$>` | |
| 19 | 0x13 | U+0013 | `<$>` | |
| 20 | 0x14 | U+0014 | `<$>` | |
| 21 | 0x15 | U+0015 | `<$>` | |
| 22 | 0x16 | U+0016 | `<$>` | |
| 23 | 0x17 | U+0017 | `<$>` | |
| 24 | 0x18 | U+0018 | `<$>` | |
| 25 | 0x19 | U+0019 | `<$>` | |
| 26 | 0x1A | U+001A | `<$>` | |
| 27 | 0x1B | U+001B | `<$>` | |
| 28 | 0x1C | U+001C | `<$>` | |
| 29 | 0x1D | U+001D | `<$>` | |
| 30 | 0x1E | U+001E | `<$>` | |
| 31 | 0x1F | U+001F | `<$>` | |
| 32 | 0x20 | 空格 | `<$ >` | |
| 33 | 0x21 | EXCLAMATION MARK | `<$!>` | |
| 34 | 0x22 | QUOTATION MARK | `<$">` | |
| 35 | 0x23 | NUMBER SIGN | `<$#>` | |
| 36 | 0x24 | DOLLAR SIGN | `<$$>` | |
| 37 | 0x25 | PERCENT SIGN | `<$%>` | |
| 38 | 0x26 | AMPERSAND | `<$&>` | |
| 39 | 0x27 | APOSTROPHE | `<$'>` | |
| 40 | 0x28 | LEFT PARENTHESIS | `<$(>` | |
| 41 | 0x29 | RIGHT PARENTHESIS | `<$)>` | |
| 42 | 0x2A | ASTERISK | `<$*>` | |
| 43 | 0x2B | PLUS SIGN | `<$+>` | |
| 44 | 0x2C | COMMA | `<$,>` | |
| 45 | 0x2D | HYPHEN-MINUS | `<$->` | |
| 46 | 0x2E | FULL STOP | `<$.>` | |
| 47 | 0x2F | SOLIDUS | `<$/>` | |
| 48 | 0x30 | DIGIT ZERO | `<$0>` | |
| 49 | 0x31 | DIGIT ONE | `<$1>` | |
| 50 | 0x32 | DIGIT TWO | `<$2>` | |
| 51 | 0x33 | DIGIT THREE | `<$3>` | |
| 52 | 0x34 | DIGIT FOUR | `<$4>` | |
| 53 | 0x35 | DIGIT FIVE | `<$5>` | |
| 54 | 0x36 | DIGIT SIX | `<$6>` | |
| 55 | 0x37 | DIGIT SEVEN | `<$7>` | |
| 56 | 0x38 | DIGIT EIGHT | `<$8>` | |
| 57 | 0x39 | DIGIT NINE | `<$9>` | |
| 58 | 0x3A | COLON | `<$:>` | |
| 59 | 0x3B | SEMICOLON | `<$;>` | |
| 60 | 0x3C | LESS-THAN SIGN | `<$<>` | |
| 61 | 0x3D | EQUALS SIGN | `<$=>` | |
| 62 | 0x3E | GREATER-THAN SIGN | `<$>>` | |
| 63 | 0x3F | QUESTION MARK | `<$?>` | |
| 64 | 0x40 | COMMERCIAL AT | `<$@>` | |
| 65 | 0x41 | LATIN CAPITAL LETTER A | `<$A>` | |
| 66 | 0x42 | LATIN CAPITAL LETTER B | `<$B>` | |
| 67 | 0x43 | LATIN CAPITAL LETTER C | `<$C>` | |
| 68 | 0x44 | LATIN CAPITAL LETTER D | `<$D>` | |
| 69 | 0x45 | LATIN CAPITAL LETTER E | `<$E>` | |
| 70 | 0x46 | LATIN CAPITAL LETTER F | `<$F>` | |
| 71 | 0x47 | LATIN CAPITAL LETTER G | `<$G>` | |
| 72 | 0x48 | LATIN CAPITAL LETTER H | `<$H>` | |
| 73 | 0x49 | LATIN CAPITAL LETTER I | `<$I>` | |
| 74 | 0x4A | LATIN CAPITAL LETTER J | `<$J>` | |
| 75 | 0x4B | LATIN CAPITAL LETTER K | `<$K>` | |
| 76 | 0x4C | LATIN CAPITAL LETTER L | `<$L>` | |
| 77 | 0x4D | LATIN CAPITAL LETTER M | `<$M>` | |
| 78 | 0x4E | LATIN CAPITAL LETTER N | `<$N>` | |
| 79 | 0x4F | LATIN CAPITAL LETTER O | `<$O>` | |
| 80 | 0x50 | LATIN CAPITAL LETTER P | `<$P>` | |
| 81 | 0x51 | LATIN CAPITAL LETTER Q | `<$Q>` | |
| 82 | 0x52 | LATIN CAPITAL LETTER R | `<$R>` | |
| 83 | 0x53 | LATIN CAPITAL LETTER S | `<$S>` | |
| 84 | 0x54 | LATIN CAPITAL LETTER T | `<$T>` | |
| 85 | 0x55 | LATIN CAPITAL LETTER U | `<$U>` | |
| 86 | 0x56 | LATIN CAPITAL LETTER V | `<$V>` | |
| 87 | 0x57 | LATIN CAPITAL LETTER W | `<$W>` | |
| 88 | 0x58 | LATIN CAPITAL LETTER X | `<$X>` | |
| 89 | 0x59 | LATIN CAPITAL LETTER Y | `<$Y>` | |
| 90 | 0x5A | LATIN CAPITAL LETTER Z | `<$Z>` | |
| 91 | 0x5B | LEFT SQUARE BRACKET | `<$[>` | |
| 92 | 0x5C | REVERSE SOLIDUS | `<$\>` | |
| 93 | 0x5D | RIGHT SQUARE BRACKET | `<$]>` | |
| 94 | 0x5E | CIRCUMFLEX ACCENT | `<$^>` | |
| 95 | 0x5F | LOW LINE | `<$_>` | |
| 96 | 0x60 | GRAVE ACCENT | `<$`>` | |
| 97 | 0x61 | LATIN SMALL LETTER A | `<$a>` | |
| 98 | 0x62 | LATIN SMALL LETTER B | `<$b>` | |
| 99 | 0x63 | LATIN SMALL LETTER C | `<$c>` | |
| 100 | 0x64 | LATIN SMALL LETTER D | `<$d>` | |
| 101 | 0x65 | LATIN SMALL LETTER E | `<$e>` | |
| 102 | 0x66 | LATIN SMALL LETTER F | `<$f>` | |
| 103 | 0x67 | LATIN SMALL LETTER G | `<$g>` | |
| 104 | 0x68 | LATIN SMALL LETTER H | `<$h>` | |
| 105 | 0x69 | LATIN SMALL LETTER I | `<$i>` | |
| 106 | 0x6A | LATIN SMALL LETTER J | `<$j>` | |
| 107 | 0x6B | LATIN SMALL LETTER K | `<$k>` | |
| 108 | 0x6C | LATIN SMALL LETTER L | `<$l>` | |
| 109 | 0x6D | LATIN SMALL LETTER M | `<$m>` | |
| 110 | 0x6E | LATIN SMALL LETTER N | `<$n>` | |
| 111 | 0x6F | LATIN SMALL LETTER O | `<$o>` | |
| 112 | 0x70 | LATIN SMALL LETTER P | `<$p>` | |
| 113 | 0x71 | LATIN SMALL LETTER Q | `<$q>` | |
| 114 | 0x72 | LATIN SMALL LETTER R | `<$r>` | |
| 115 | 0x73 | LATIN SMALL LETTER S | `<$s>` | |
| 116 | 0x74 | LATIN SMALL LETTER T | `<$t>` | |
| 117 | 0x75 | LATIN SMALL LETTER U | `<$u>` | |
| 118 | 0x76 | LATIN SMALL LETTER V | `<$v>` | |
| 119 | 0x77 | LATIN SMALL LETTER W | `<$w>` | |
| 120 | 0x78 | LATIN SMALL LETTER X | `<$x>` | |
| 121 | 0x79 | LATIN SMALL LETTER Y | `<$y>` | |
| 122 | 0x7A | LATIN SMALL LETTER Z | `<$z>` | |
| 123 | 0x7B | LEFT CURLY BRACKET | `<${>` | |
| 124 | 0x7C | VERTICAL LINE | `<$\|>` | |
| 125 | 0x7D | RIGHT CURLY BRACKET | `<$}>` | |
| 126 | 0x7E | TILDE | `<$~>` | |
| 127 | 0x7F | U+007F | `<$>` | |

