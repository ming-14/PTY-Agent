# Interface: ITerminalOptions

An object containing options for the terminal.

## Hierarchy

  * **ITerminalOptions**


## Index

### Properties

  * [allowProposedApi](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-allowproposedapi)
  * [allowTransparency](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-allowtransparency)
  * [altClickMovesCursor](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-altclickmovescursor)
  * [convertEol](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-converteol)
  * [cursorBlink](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-cursorblink)
  * [cursorInactiveStyle](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-cursorinactivestyle)
  * [cursorStyle](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-cursorstyle)
  * [cursorWidth](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-cursorwidth)
  * [customGlyphs](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-customglyphs)
  * [disableStdin](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-disablestdin)
  * [documentOverride](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-documentoverride)
  * [drawBoldTextInBrightColors](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-drawboldtextinbrightcolors)
  * [fastScrollSensitivity](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-fastscrollsensitivity)
  * [fontFamily](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-fontfamily)
  * [fontSize](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-fontsize)
  * [fontWeight](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-fontweight)
  * [fontWeightBold](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-fontweightbold)
  * [ignoreBracketedPasteMode](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-ignorebracketedpastemode)
  * [letterSpacing](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-letterspacing)
  * [lineHeight](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-lineheight)
  * [linkHandler](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-linkhandler)
  * [logLevel](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-loglevel)
  * [logger](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-logger)
  * [macOptionClickForcesSelection](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-macoptionclickforcesselection)
  * [macOptionIsMeta](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-macoptionismeta)
  * [minimumContrastRatio](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-minimumcontrastratio)
  * [overviewRuler](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-overviewruler)
  * [reflowCursorLine](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-reflowcursorline)
  * [rescaleOverlappingGlyphs](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-rescaleoverlappingglyphs)
  * [rightClickSelectsWord](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-rightclickselectsword)
  * [screenReaderMode](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-screenreadermode)
  * [scrollOnEraseInDisplay](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-scrolloneraseindisplay)
  * [scrollOnUserInput](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-scrollonuserinput)
  * [scrollSensitivity](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-scrollsensitivity)
  * [scrollback](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-scrollback)
  * [smoothScrollDuration](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-smoothscrollduration)
  * [tabStopWidth](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-tabstopwidth)
  * [theme](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-theme)
  * [windowOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-windowoptions)
  * [windowsPty](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-windowspty)
  * [wordSeparator](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/#optional-wordseparator)


## Properties

### `Optional` allowProposedApi

• **allowProposedApi**? : _boolean_

_Defined in[xterm.d.ts:32](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L32)_

Whether to allow the use of proposed API. When false, any usage of APIs marked as experimental/proposed will throw an error. The default is false.

* * *

### `Optional` allowTransparency

• **allowTransparency**? : _boolean_

_Defined in[xterm.d.ts:40](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L40)_

Whether background should support non-opaque color. It must be set before executing the `Terminal.open()` method and can’t be changed later without executing it again. Note that enabling this can negatively impact performance.

* * *

### `Optional` altClickMovesCursor

• **altClickMovesCursor**? : _boolean_

_Defined in[xterm.d.ts:46](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L46)_

If enabled, alt + click will move the prompt cursor to position underneath the mouse. The default is true.

* * *

### `Optional` convertEol

• **convertEol**? : _boolean_

_Defined in[xterm.d.ts:58](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L58)_

When enabled the cursor will be set to the beginning of the next line with every new line. This is equivalent to sending `\r\n` for each `\n`. Normally the settings of the underlying PTY (`termios`) deal with the translation of `\n` to `\r\n` and this setting should not be used. If you deal with data from a non-PTY related source, this settings might be useful.

**`see`** https://pubs.opengroup.org/onlinepubs/007904975/basedefs/termios.h.html

* * *

### `Optional` cursorBlink

• **cursorBlink**? : _boolean_

_Defined in[xterm.d.ts:63](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L63)_

Whether the cursor blinks.

* * *

### `Optional` cursorInactiveStyle

• **cursorInactiveStyle**? : *“outline” | “block” | “bar” | “underline” | “none”*  
---|---|---|---|---  
  
_Defined in[xterm.d.ts:78](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L78)_

The style of the cursor when the terminal is not focused.

* * *

### `Optional` cursorStyle

• **cursorStyle**? : *“block” | “underline” | “bar”*  
---|---|---  
  
_Defined in[xterm.d.ts:68](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L68)_

The style of the cursor when the terminal is focused.

* * *

### `Optional` cursorWidth

• **cursorWidth**? : _number_

_Defined in[xterm.d.ts:73](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L73)_

The width of the cursor in CSS pixels when `cursorStyle` is set to ‘bar’.

* * *

### `Optional` customGlyphs

• **customGlyphs**? : _boolean_

_Defined in[xterm.d.ts:87](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L87)_

Whether to draw custom glyphs for block element and box drawing characters instead of using the font. This should typically result in better rendering with continuous lines, even when line height and letter spacing is used. Note that this doesn’t work with the DOM renderer which renders all characters using the font. The default is true.

* * *

### `Optional` disableStdin

• **disableStdin**? : _boolean_

_Defined in[xterm.d.ts:92](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L92)_

Whether input should be disabled.

* * *

### `Optional` documentOverride

• **documentOverride**? : *any | null*  
---|---  
  
_Defined in[xterm.d.ts:103](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L103)_

A {@link Document} to use instead of the one that xterm.js was attached to. The purpose of this is to improve support in multi-window applications where HTML elements may be references across multiple windows which can cause problems with `instanceof`.

The type is `any` because using `Document` can cause TS to have performance/compiler problems.

* * *

### `Optional` drawBoldTextInBrightColors

• **drawBoldTextInBrightColors**? : _boolean_

_Defined in[xterm.d.ts:108](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L108)_

Whether to draw bold text in bright colors. The default is true.

* * *

### `Optional` fastScrollSensitivity

• **fastScrollSensitivity**? : _number_

_Defined in[xterm.d.ts:113](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L113)_

The scroll speed multiplier used for fast scrolling when `Alt` is held.

* * *

### `Optional` fontFamily

• **fontFamily**? : _string_

_Defined in[xterm.d.ts:123](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L123)_

The font family used to render text.

* * *

### `Optional` fontSize

• **fontSize**? : _number_

_Defined in[xterm.d.ts:118](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L118)_

The font size used to render text.

* * *

### `Optional` fontWeight

• **fontWeight**? : _[FontWeight](https://xtermjs.org/docs/api/terminal/modules/xterm/#fontweight)_

_Defined in[xterm.d.ts:128](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L128)_

The font weight used to render non-bold text.

* * *

### `Optional` fontWeightBold

• **fontWeightBold**? : _[FontWeight](https://xtermjs.org/docs/api/terminal/modules/xterm/#fontweight)_

_Defined in[xterm.d.ts:133](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L133)_

The font weight used to render bold text.

* * *

### `Optional` ignoreBracketedPasteMode

• **ignoreBracketedPasteMode**? : _boolean_

_Defined in[xterm.d.ts:140](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L140)_

Whether to ignore the bracketed paste mode. When true, this will always paste without the `\x1b[200~` and `\x1b[201~` sequences, even when the shell enables bracketed mode.

* * *

### `Optional` letterSpacing

• **letterSpacing**? : _number_

_Defined in[xterm.d.ts:145](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L145)_

The spacing in whole pixels between characters.

* * *

### `Optional` lineHeight

• **lineHeight**? : _number_

_Defined in[xterm.d.ts:150](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L150)_

The line height used to render text.

* * *

### `Optional` linkHandler

• **linkHandler**? : *[ILinkHandler](https://xtermjs.org/docs/api/terminal/interfaces/ilinkhandler/) | null*  
---|---  
  
_Defined in[xterm.d.ts:163](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L163)_

The handler for OSC 8 hyperlinks. Links will use the `confirm` browser API with a strongly worded warning if no link handler is set.

When setting this, consider the security of users opening these links, at a minimum there should be a tooltip or a prompt when hovering or activating the link respectively. An example of what might be possible is a terminal app writing link in the form `javascript:...` that runs some javascript, a safe approach to prevent that is to validate the link starts with http(s)://.

* * *

### `Optional` logLevel

• **logLevel**? : _[LogLevel](https://xtermjs.org/docs/api/terminal/modules/xterm/#loglevel)_

_Defined in[xterm.d.ts:176](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L176)_

What log level to use, this will log for all levels below and including what is set:

  1. trace
  2. debug
  3. info (default)
  4. warn
  5. error
  6. off


* * *

### `Optional` logger

• **logger**? : *[ILogger](https://xtermjs.org/docs/api/terminal/interfaces/ilogger/) | null*  
---|---  
  
_Defined in[xterm.d.ts:181](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L181)_

A logger to use instead of `console`.

* * *

### `Optional` macOptionClickForcesSelection

• **macOptionClickForcesSelection**? : _boolean_

_Defined in[xterm.d.ts:195](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L195)_

Whether holding a modifier key will force normal selection behavior, regardless of whether the terminal is in mouse events mode. This will also prevent mouse events from being emitted by the terminal. For example, this allows you to use xterm.js’ regular selection inside tmux with mouse mode enabled.

* * *

### `Optional` macOptionIsMeta

• **macOptionIsMeta**? : _boolean_

_Defined in[xterm.d.ts:186](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L186)_

Whether to treat option as the meta key.

* * *

### `Optional` minimumContrastRatio

• **minimumContrastRatio**? : _number_

_Defined in[xterm.d.ts:207](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L207)_

The minimum contrast ratio for text in the terminal, setting this will change the foreground color dynamically depending on whether the contrast ratio is met. Example values:

  * 1: The default, do nothing.
  * 4.5: Minimum for WCAG AA compliance.
  * 7: Minimum for WCAG AAA compliance.
  * 21: White on black or black on white.


* * *

### `Optional` overviewRuler

• **overviewRuler**? : _[IOverviewRulerOptions](https://xtermjs.org/docs/api/terminal/interfaces/ioverviewruleroptions/)_

_Defined in[xterm.d.ts:321](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L321)_

Controls the visibility and style of the overview ruler which visualizes decorations underneath the scroll bar.

* * *

### `Optional` reflowCursorLine

• **reflowCursorLine**? : _boolean_

_Defined in[xterm.d.ts:214](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L214)_

Whether to reflow the line containing the cursor when the terminal is resized. Defaults to false, because shells usually handle this themselves.

* * *

### `Optional` rescaleOverlappingGlyphs

• **rescaleOverlappingGlyphs**? : _boolean_

_Defined in[xterm.d.ts:231](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L231)_

Whether to rescale glyphs horizontally that are a single cell wide but have glyphs that would overlap following cell(s). This typically happens for ambiguous width characters (eg. the roman numeral characters U+2160+) which aren’t featured in monospace fonts. This is an important feature for achieving GB18030 compliance.

The following glyphs will never be rescaled:

  * Emoji glyphs
  * Powerline glyphs
  * Nerd font glyphs


Note that this doesn’t work with the DOM renderer. The default is false.

* * *

### `Optional` rightClickSelectsWord

• **rightClickSelectsWord**? : _boolean_

_Defined in[xterm.d.ts:237](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L237)_

Whether to select the word under the cursor on right click, this is standard behavior in a lot of macOS applications.

* * *

### `Optional` screenReaderMode

• **screenReaderMode**? : _boolean_

_Defined in[xterm.d.ts:244](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L244)_

Whether screen reader support is enabled. When on this will expose supporting elements in the DOM to support NVDA on Windows and VoiceOver on macOS.

* * *

### `Optional` scrollOnEraseInDisplay

• **scrollOnEraseInDisplay**? : _boolean_

_Defined in[xterm.d.ts:258](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L258)_

If enabled the Erase in Display All (ED2) escape sequence will push erased text to scrollback, instead of clearing only the viewport portion. This emulates PuTTY’s default clear screen behavior.

* * *

### `Optional` scrollOnUserInput

• **scrollOnUserInput**? : _boolean_

_Defined in[xterm.d.ts:264](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L264)_

Whether to scroll to the bottom whenever there is some user input. The default is true.

* * *

### `Optional` scrollSensitivity

• **scrollSensitivity**? : _number_

_Defined in[xterm.d.ts:269](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L269)_

The scrolling speed multiplier used for adjusting normal scrolling speed.

* * *

### `Optional` scrollback

• **scrollback**? : _number_

_Defined in[xterm.d.ts:251](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L251)_

The amount of scrollback in the terminal. Scrollback is the amount of rows that are retained when lines are scrolled beyond the initial viewport. Defaults to 1000.

* * *

### `Optional` smoothScrollDuration

• **smoothScrollDuration**? : _number_

_Defined in[xterm.d.ts:275](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L275)_

The duration to smoothly scroll between the origin and the target in milliseconds. Set to 0 to disable smooth scrolling and scroll instantly.

* * *

### `Optional` tabStopWidth

• **tabStopWidth**? : _number_

_Defined in[xterm.d.ts:280](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L280)_

The size of tab stops in the terminal.

* * *

### `Optional` theme

• **theme**? : _[ITheme](https://xtermjs.org/docs/api/terminal/interfaces/itheme/)_

_Defined in[xterm.d.ts:285](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L285)_

The color theme of the terminal.

* * *

### `Optional` windowOptions

• **windowOptions**? : _[IWindowOptions](https://xtermjs.org/docs/api/terminal/interfaces/iwindowoptions/)_

_Defined in[xterm.d.ts:315](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L315)_

Enable various window manipulation and report features. All features are disabled by default for security reasons.

* * *

### `Optional` windowsPty

• **windowsPty**? : _[IWindowsPty](https://xtermjs.org/docs/api/terminal/interfaces/iwindowspty/)_

_Defined in[xterm.d.ts:303](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L303)_

Compatibility information when the pty is known to be hosted on Windows. Setting this will turn on certain heuristics/workarounds depending on the values:

  * `if (backend !== undefined || buildNumber !== undefined)`
    * When increasing the rows in the terminal, the amount increased into the scrollback. This is done because ConPTY does not behave like expect scrollback to come back into the viewport, instead it makes empty rows at of the viewport. Not having this behavior can result in missing data as the rows get replaced.
  * `if !(backend === 'conpty' && buildNumber >= 21376)`
    * Reflow is disabled
    * Lines are assumed to be wrapped if the last character of the line is not whitespace.


* * *

### `Optional` wordSeparator

• **wordSeparator**? : _string_

_Defined in[xterm.d.ts:309](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L309)_

A string containing all characters that are considered word separated by the double click to select work logic.