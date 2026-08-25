# Interface: IUnicodeVersionProvider

(EXPERIMENTAL) Unicode version provider. Used to register custom Unicode versions with `Terminal.unicode.register`.

## Hierarchy

  * **IUnicodeVersionProvider**


## Index

### Properties

  * [version](https://xtermjs.org/docs/api/terminal/interfaces/iunicodeversionprovider/#readonly-version)


### Methods

  * [charProperties](https://xtermjs.org/docs/api/terminal/interfaces/iunicodeversionprovider/#charproperties)
  * [wcwidth](https://xtermjs.org/docs/api/terminal/interfaces/iunicodeversionprovider/#wcwidth)


## Properties

### `Readonly` version

• **version** : _string_

_Defined in[xterm.d.ts:1875](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1875)_

String indicating the Unicode version provided.

## Methods

### charProperties

▸ **charProperties**(`codepoint`: number, `preceding`: number): _number_

_Defined in[xterm.d.ts:1881](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1881)_

**Parameters:**

Name | Type  
---|---  
`codepoint` | number  
`preceding` | number  
  
**Returns:** _number_

* * *

### wcwidth

▸ **wcwidth**(`codepoint`: number): *0 | 1 | 2*  
---|---|---  
  
_Defined in[xterm.d.ts:1880](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1880)_

Unicode version dependent wcwidth implementation.

**Parameters:**

Name | Type  
---|---  
`codepoint` | number  
**Returns:** *0 | 1 | 2*  
---|---|---