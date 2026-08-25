# Interface: IUnicodeHandling

(EXPERIMENTAL) Unicode handling interface.

## Hierarchy

  * **IUnicodeHandling**


## Index

### Properties

  * [activeVersion](https://xtermjs.org/docs/api/terminal/interfaces/iunicodehandling/#activeversion)
  * [versions](https://xtermjs.org/docs/api/terminal/interfaces/iunicodehandling/#readonly-versions)


### Methods

  * [register](https://xtermjs.org/docs/api/terminal/interfaces/iunicodehandling/#register)


## Properties

### activeVersion

• **activeVersion** : _string_

_Defined in[xterm.d.ts:1901](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1901)_

Getter/setter for active Unicode version.

* * *

### `Readonly` versions

• **versions** : _ReadonlyArray‹string›_

_Defined in[xterm.d.ts:1896](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1896)_

Registered Unicode versions.

## Methods

### register

▸ **register**(`provider`: [IUnicodeVersionProvider](https://xtermjs.org/docs/api/terminal/interfaces/iunicodeversionprovider/)): _void_

_Defined in[xterm.d.ts:1891](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1891)_

Register a custom Unicode version provider.

**Parameters:**

Name | Type  
---|---  
`provider` | [IUnicodeVersionProvider](https://xtermjs.org/docs/api/terminal/interfaces/iunicodeversionprovider/)  
  
**Returns:** _void_