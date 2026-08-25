# Interface: IBufferCell

Represents a single cell in the terminal’s buffer.

## Hierarchy

  * **IBufferCell**


## Index

### Methods

  * [getBgColor](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#getbgcolor)
  * [getBgColorMode](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#getbgcolormode)
  * [getChars](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#getchars)
  * [getCode](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#getcode)
  * [getFgColor](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#getfgcolor)
  * [getFgColorMode](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#getfgcolormode)
  * [getWidth](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#getwidth)
  * [isAttributeDefault](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isattributedefault)
  * [isBgDefault](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isbgdefault)
  * [isBgPalette](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isbgpalette)
  * [isBgRGB](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isbgrgb)
  * [isBlink](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isblink)
  * [isBold](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isbold)
  * [isDim](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isdim)
  * [isFgDefault](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isfgdefault)
  * [isFgPalette](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isfgpalette)
  * [isFgRGB](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isfgrgb)
  * [isInverse](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isinverse)
  * [isInvisible](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isinvisible)
  * [isItalic](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isitalic)
  * [isOverline](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isoverline)
  * [isStrikethrough](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isstrikethrough)
  * [isUnderline](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/#isunderline)


## Methods

### getBgColor

▸ **getBgColor**(): _number_

_Defined in[xterm.d.ts:1703](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1703)_

Gets a cell’s background color number, this differs depending on what the color mode of the cell is:

  * Default: This should be 0, representing the default background color (CSI 49 m).
  * Palette: This is a number from 0 to 255 of ANSI colors (CSI 4(0-7) m, CSI 10(0-7) m, CSI 48 ; 5 ; 0-255 m).
  * RGB: A hex value representing a ‘true color’: 0xRRGGBB (CSI 4 8 ; 2 ; Pi ; Pr ; Pg ; Pb)


**Returns:** _number_

* * *

### getBgColorMode

▸ **getBgColorMode**(): _number_

_Defined in[xterm.d.ts:1677](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1677)_

Gets the number representation of the background color mode, this can be used to perform quick comparisons of 2 cells to see if they’re the same. Use `isBgRGB`, `isBgPalette` and `isBgDefault` to check what color mode a cell is.

**Returns:** _number_

* * *

### getChars

▸ **getChars**(): _string_

_Defined in[xterm.d.ts:1655](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1655)_

The character(s) within the cell. Examples of what this can contain:

  * A normal width character
  * A wide character (eg. CJK)
  * An emoji


**Returns:** _string_

* * *

### getCode

▸ **getCode**(): _number_

_Defined in[xterm.d.ts:1661](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1661)_

Gets the UTF32 codepoint of single characters, if content is a combined string it returns the codepoint of the last character in the string.

**Returns:** _number_

* * *

### getFgColor

▸ **getFgColor**(): _number_

_Defined in[xterm.d.ts:1690](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1690)_

Gets a cell’s foreground color number, this differs depending on what the color mode of the cell is:

  * Default: This should be 0, representing the default foreground color (CSI 39 m).
  * Palette: This is a number from 0 to 255 of ANSI colors (CSI 3(0-7) m, CSI 9(0-7) m, CSI 38 ; 5 ; 0-255 m).
  * RGB: A hex value representing a ‘true color’: 0xRRGGBB. (CSI 3 8 ; 2 ; Pi ; Pr ; Pg ; Pb)


**Returns:** _number_

* * *

### getFgColorMode

▸ **getFgColorMode**(): _number_

_Defined in[xterm.d.ts:1669](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1669)_

Gets the number representation of the foreground color mode, this can be used to perform quick comparisons of 2 cells to see if they’re the same. Use `isFgRGB`, `isFgPalette` and `isFgDefault` to check what color mode a cell is.

**Returns:** _number_

* * *

### getWidth

▸ **getWidth**(): _number_

_Defined in[xterm.d.ts:1646](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1646)_

The width of the character. Some examples:

  * `1` for most cells.
  * `2` for wide character like CJK glyphs.
  * `0` for cells immediately following cells with a width of `2`.


**Returns:** _number_

* * *

### isAttributeDefault

▸ **isAttributeDefault**(): _boolean_

_Defined in[xterm.d.ts:1738](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1738)_

Whether the cell has the default attribute (no color or style).

**Returns:** _boolean_

* * *

### isBgDefault

▸ **isBgDefault**(): _boolean_

_Defined in[xterm.d.ts:1735](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1735)_

Whether the cell is using the default background color mode.

**Returns:** _boolean_

* * *

### isBgPalette

▸ **isBgPalette**(): _boolean_

_Defined in[xterm.d.ts:1731](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1731)_

Whether the cell is using the palette background color mode.

**Returns:** _boolean_

* * *

### isBgRGB

▸ **isBgRGB**(): _boolean_

_Defined in[xterm.d.ts:1727](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1727)_

Whether the cell is using the RGB background color mode.

**Returns:** _boolean_

* * *

### isBlink

▸ **isBlink**(): _number_

_Defined in[xterm.d.ts:1714](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1714)_

Whether the cell has the blink attribute (CSI 5 m).

**Returns:** _number_

* * *

### isBold

▸ **isBold**(): _number_

_Defined in[xterm.d.ts:1706](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1706)_

Whether the cell has the bold attribute (CSI 1 m).

**Returns:** _number_

* * *

### isDim

▸ **isDim**(): _number_

_Defined in[xterm.d.ts:1710](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1710)_

Whether the cell has the dim attribute (CSI 2 m).

**Returns:** _number_

* * *

### isFgDefault

▸ **isFgDefault**(): _boolean_

_Defined in[xterm.d.ts:1733](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1733)_

Whether the cell is using the default foreground color mode.

**Returns:** _boolean_

* * *

### isFgPalette

▸ **isFgPalette**(): _boolean_

_Defined in[xterm.d.ts:1729](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1729)_

Whether the cell is using the palette foreground color mode.

**Returns:** _boolean_

* * *

### isFgRGB

▸ **isFgRGB**(): _boolean_

_Defined in[xterm.d.ts:1725](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1725)_

Whether the cell is using the RGB foreground color mode.

**Returns:** _boolean_

* * *

### isInverse

▸ **isInverse**(): _number_

_Defined in[xterm.d.ts:1716](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1716)_

Whether the cell has the inverse attribute (CSI 7 m).

**Returns:** _number_

* * *

### isInvisible

▸ **isInvisible**(): _number_

_Defined in[xterm.d.ts:1718](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1718)_

Whether the cell has the invisible attribute (CSI 8 m).

**Returns:** _number_

* * *

### isItalic

▸ **isItalic**(): _number_

_Defined in[xterm.d.ts:1708](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1708)_

Whether the cell has the italic attribute (CSI 3 m).

**Returns:** _number_

* * *

### isOverline

▸ **isOverline**(): _number_

_Defined in[xterm.d.ts:1722](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1722)_

Whether the cell has the overline attribute (CSI 53 m).

**Returns:** _number_

* * *

### isStrikethrough

▸ **isStrikethrough**(): _number_

_Defined in[xterm.d.ts:1720](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1720)_

Whether the cell has the strikethrough attribute (CSI 9 m).

**Returns:** _number_

* * *

### isUnderline

▸ **isUnderline**(): _number_

_Defined in[xterm.d.ts:1712](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1712)_

Whether the cell has the underline attribute (CSI 4 m).

**Returns:** _number_