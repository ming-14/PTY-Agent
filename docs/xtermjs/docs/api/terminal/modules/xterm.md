# Module: “xterm”

**`license`** MIT

This contains the type declarations for the xterm.js library. Note that some interfaces differ between this file and the actual implementation in src/, that’s because this file declares the _public_ API which is intended to be stable and consumed by external programs.

## Index

### Classes

  * [Terminal](https://xtermjs.org/docs/api/terminal/classes/terminal/)


### Interfaces

  * [IBuffer](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer/)
  * [IBufferCell](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercell/)
  * [IBufferCellPosition](https://xtermjs.org/docs/api/terminal/interfaces/ibuffercellposition/)
  * [IBufferElementProvider](https://xtermjs.org/docs/api/terminal/interfaces/ibufferelementprovider/)
  * [IBufferLine](https://xtermjs.org/docs/api/terminal/interfaces/ibufferline/)
  * [IBufferNamespace](https://xtermjs.org/docs/api/terminal/interfaces/ibuffernamespace/)
  * [IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/)
  * [IDecoration](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/)
  * [IDecorationOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/)
  * [IDecorationOverviewRulerOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoverviewruleroptions/)
  * [IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)
  * [IDisposableWithEvent](https://xtermjs.org/docs/api/terminal/interfaces/idisposablewithevent/)
  * [IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)
  * [IFunctionIdentifier](https://xtermjs.org/docs/api/terminal/interfaces/ifunctionidentifier/)
  * [ILink](https://xtermjs.org/docs/api/terminal/interfaces/ilink/)
  * [ILinkDecorations](https://xtermjs.org/docs/api/terminal/interfaces/ilinkdecorations/)
  * [ILinkHandler](https://xtermjs.org/docs/api/terminal/interfaces/ilinkhandler/)
  * [ILinkProvider](https://xtermjs.org/docs/api/terminal/interfaces/ilinkprovider/)
  * [ILocalizableStrings](https://xtermjs.org/docs/api/terminal/interfaces/ilocalizablestrings/)
  * [ILogger](https://xtermjs.org/docs/api/terminal/interfaces/ilogger/)
  * [IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/)
  * [IModes](https://xtermjs.org/docs/api/terminal/interfaces/imodes/)
  * [IOverviewRulerOptions](https://xtermjs.org/docs/api/terminal/interfaces/ioverviewruleroptions/)
  * [IParser](https://xtermjs.org/docs/api/terminal/interfaces/iparser/)
  * [ITerminalAddon](https://xtermjs.org/docs/api/terminal/interfaces/iterminaladdon/)
  * [ITerminalInitOnlyOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminalinitonlyoptions/)
  * [ITerminalOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/)
  * [ITheme](https://xtermjs.org/docs/api/terminal/interfaces/itheme/)
  * [IUnicodeHandling](https://xtermjs.org/docs/api/terminal/interfaces/iunicodehandling/)
  * [IUnicodeVersionProvider](https://xtermjs.org/docs/api/terminal/interfaces/iunicodeversionprovider/)
  * [IViewportRange](https://xtermjs.org/docs/api/terminal/interfaces/iviewportrange/)
  * [IViewportRangePosition](https://xtermjs.org/docs/api/terminal/interfaces/iviewportrangeposition/)
  * [IWindowOptions](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/)
  * [IWindowsPty](https://xtermjs.org/docs/api/terminal/interfaces/iwindowspty/)


### Type aliases

  * [FontWeight](https://xtermjs.org/docs/api/terminal/modules/xterm/#fontweight)
  * [LogLevel](https://xtermjs.org/docs/api/terminal/modules/xterm/#loglevel)


## Type aliases

### FontWeight

Ƭ **FontWeight** : *“normal” | “bold” | “100” | “200” | “300” | “400” | “500” | “600” | “700” | “800” | “900” | number*  
---|---|---|---|---|---|---|---|---|---|---|---  
  
_Defined in[xterm.d.ts:16](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L16)_

A string or number representing text font weight.

* * *

### LogLevel

Ƭ **LogLevel** : *“trace” | “debug” | “info” | “warn” | “error” | “off”*  
---|---|---|---|---|---  
  
_Defined in[xterm.d.ts:21](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L21)_

A string representing log level.