# Class: Terminal

The class that represents an xterm.js terminal.

## Hierarchy

  * **Terminal**


## Implements

  * [IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)


## Index

### Constructors

  * [constructor](https://xtermjs.org/docs/api/terminal/classes/terminal/#constructor)


### Properties

  * [buffer](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-buffer)
  * [cols](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-cols)
  * [element](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-element)
  * [markers](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-markers)
  * [modes](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-modes)
  * [onBell](https://xtermjs.org/docs/api/terminal/classes/terminal/#onbell)
  * [onBinary](https://xtermjs.org/docs/api/terminal/classes/terminal/#onbinary)
  * [onCursorMove](https://xtermjs.org/docs/api/terminal/classes/terminal/#oncursormove)
  * [onData](https://xtermjs.org/docs/api/terminal/classes/terminal/#ondata)
  * [onKey](https://xtermjs.org/docs/api/terminal/classes/terminal/#onkey)
  * [onLineFeed](https://xtermjs.org/docs/api/terminal/classes/terminal/#onlinefeed)
  * [onRender](https://xtermjs.org/docs/api/terminal/classes/terminal/#onrender)
  * [onResize](https://xtermjs.org/docs/api/terminal/classes/terminal/#onresize)
  * [onScroll](https://xtermjs.org/docs/api/terminal/classes/terminal/#onscroll)
  * [onSelectionChange](https://xtermjs.org/docs/api/terminal/classes/terminal/#onselectionchange)
  * [onTitleChange](https://xtermjs.org/docs/api/terminal/classes/terminal/#ontitlechange)
  * [onWriteParsed](https://xtermjs.org/docs/api/terminal/classes/terminal/#onwriteparsed)
  * [options](https://xtermjs.org/docs/api/terminal/classes/terminal/#options)
  * [parser](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-parser)
  * [rows](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-rows)
  * [textarea](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-textarea)
  * [unicode](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-unicode)
  * [strings](https://xtermjs.org/docs/api/terminal/classes/terminal/#static-strings)


### Methods

  * [attachCustomKeyEventHandler](https://xtermjs.org/docs/api/terminal/classes/terminal/#attachcustomkeyeventhandler)
  * [attachCustomWheelEventHandler](https://xtermjs.org/docs/api/terminal/classes/terminal/#attachcustomwheeleventhandler)
  * [blur](https://xtermjs.org/docs/api/terminal/classes/terminal/#blur)
  * [clear](https://xtermjs.org/docs/api/terminal/classes/terminal/#clear)
  * [clearSelection](https://xtermjs.org/docs/api/terminal/classes/terminal/#clearselection)
  * [clearTextureAtlas](https://xtermjs.org/docs/api/terminal/classes/terminal/#cleartextureatlas)
  * [deregisterCharacterJoiner](https://xtermjs.org/docs/api/terminal/classes/terminal/#deregistercharacterjoiner)
  * [dispose](https://xtermjs.org/docs/api/terminal/classes/terminal/#dispose)
  * [focus](https://xtermjs.org/docs/api/terminal/classes/terminal/#focus)
  * [getSelection](https://xtermjs.org/docs/api/terminal/classes/terminal/#getselection)
  * [getSelectionPosition](https://xtermjs.org/docs/api/terminal/classes/terminal/#getselectionposition)
  * [hasSelection](https://xtermjs.org/docs/api/terminal/classes/terminal/#hasselection)
  * [input](https://xtermjs.org/docs/api/terminal/classes/terminal/#input)
  * [loadAddon](https://xtermjs.org/docs/api/terminal/classes/terminal/#loadaddon)
  * [open](https://xtermjs.org/docs/api/terminal/classes/terminal/#open)
  * [paste](https://xtermjs.org/docs/api/terminal/classes/terminal/#paste)
  * [refresh](https://xtermjs.org/docs/api/terminal/classes/terminal/#refresh)
  * [registerCharacterJoiner](https://xtermjs.org/docs/api/terminal/classes/terminal/#registercharacterjoiner)
  * [registerDecoration](https://xtermjs.org/docs/api/terminal/classes/terminal/#registerdecoration)
  * [registerLinkProvider](https://xtermjs.org/docs/api/terminal/classes/terminal/#registerlinkprovider)
  * [registerMarker](https://xtermjs.org/docs/api/terminal/classes/terminal/#registermarker)
  * [reset](https://xtermjs.org/docs/api/terminal/classes/terminal/#reset)
  * [resize](https://xtermjs.org/docs/api/terminal/classes/terminal/#resize)
  * [scrollLines](https://xtermjs.org/docs/api/terminal/classes/terminal/#scrolllines)
  * [scrollPages](https://xtermjs.org/docs/api/terminal/classes/terminal/#scrollpages)
  * [scrollToBottom](https://xtermjs.org/docs/api/terminal/classes/terminal/#scrolltobottom)
  * [scrollToLine](https://xtermjs.org/docs/api/terminal/classes/terminal/#scrolltoline)
  * [scrollToTop](https://xtermjs.org/docs/api/terminal/classes/terminal/#scrolltotop)
  * [select](https://xtermjs.org/docs/api/terminal/classes/terminal/#select)
  * [selectAll](https://xtermjs.org/docs/api/terminal/classes/terminal/#selectall)
  * [selectLines](https://xtermjs.org/docs/api/terminal/classes/terminal/#selectlines)
  * [write](https://xtermjs.org/docs/api/terminal/classes/terminal/#write)
  * [writeln](https://xtermjs.org/docs/api/terminal/classes/terminal/#writeln)


## Constructors

### constructor

\+ **new Terminal**(`options?`: [ITerminalOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/) & [ITerminalInitOnlyOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminalinitonlyoptions/)): _[Terminal](https://xtermjs.org/docs/api/terminal/classes/terminal/)_

_Defined in[xterm.d.ts:904](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L904)_

Creates a new `Terminal` object.

**Parameters:**

Name | Type | Description  
---|---|---  
`options?` | [ITerminalOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/) & [ITerminalInitOnlyOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminalinitonlyoptions/) | An object containing a set of options.  
  
**Returns:** _[Terminal](https://xtermjs.org/docs/api/terminal/classes/terminal/)_

## Properties

### `Readonly` buffer

• **buffer** : _[IBufferNamespace](https://xtermjs.org/docs/api/terminal/interfaces/ibuffernamespace/)_

_Defined in[xterm.d.ts:841](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L841)_

Access to the terminal’s normal and alt buffer.

* * *

### `Readonly` cols

• **cols** : _number_

_Defined in[xterm.d.ts:836](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L836)_

The number of columns in the terminal’s viewport. Use `ITerminalOptions.cols` to set this in the constructor and `Terminal.resize` for when the terminal exists.

* * *

### `Readonly` element

• **element** : *HTMLElement | undefined*  
---|---  
  
_Defined in[xterm.d.ts:817](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L817)_

The element containing the terminal.

* * *

### `Readonly` markers

• **markers** : _ReadonlyArray‹[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/)›_

_Defined in[xterm.d.ts:847](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L847)_

(EXPERIMENTAL) Get all markers registered against the buffer. If the alt buffer is active this will always return [].

* * *

### `Readonly` modes

• **modes** : _[IModes](https://xtermjs.org/docs/api/terminal/interfaces/imodes/)_

_Defined in[xterm.d.ts:863](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L863)_

Gets the terminal modes as set by SM/DECSET.

* * *

### onBell

• **onBell** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Defined in[xterm.d.ts:917](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L917)_

Adds an event listener for when the bell is triggered.

**`returns`** an `IDisposable` to stop listening.

* * *

### onBinary

• **onBinary** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹string›_

_Defined in[xterm.d.ts:928](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L928)_

Adds an event listener for when a binary event fires. This is used to enable non UTF-8 conformant binary messages to be sent to the backend. Currently this is only used for a certain type of mouse reports that happen to be not UTF-8 compatible. The event value is a JS string, pass it to the underlying pty as binary data, e.g. `pty.write(Buffer.from(data, 'binary'))`.

**`returns`** an `IDisposable` to stop listening.

* * *

### onCursorMove

• **onCursorMove** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Defined in[xterm.d.ts:934](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L934)_

Adds an event listener for the cursor moves.

**`returns`** an `IDisposable` to stop listening.

* * *

### onData

• **onData** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹string›_

_Defined in[xterm.d.ts:943](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L943)_

Adds an event listener for when a data event fires. This happens for example when the user types or pastes into the terminal. The event value is whatever `string` results, in a typical setup, this should be passed on to the backing pty.

**`returns`** an `IDisposable` to stop listening.

* * *

### onKey

• **onKey** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹object›_

_Defined in[xterm.d.ts:951](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L951)_

Adds an event listener for when a key is pressed. The event value contains the string that will be sent in the data event as well as the DOM event that triggered it.

**`returns`** an `IDisposable` to stop listening.

* * *

### onLineFeed

• **onLineFeed** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Defined in[xterm.d.ts:957](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L957)_

Adds an event listener for when a line feed is added.

**`returns`** an `IDisposable` to stop listening.

* * *

### onRender

• **onRender** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹object›_

_Defined in[xterm.d.ts:965](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L965)_

Adds an event listener for when rows are rendered. The event value contains the start row and end rows of the rendered area (ranges from `0` to `Terminal.rows - 1`).

**`returns`** an `IDisposable` to stop listening.

* * *

### onResize

• **onResize** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹object›_

_Defined in[xterm.d.ts:983](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L983)_

Adds an event listener for when the terminal is resized. The event value contains the new size.

**`returns`** an `IDisposable` to stop listening.

* * *

### onScroll

• **onScroll** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹number›_

_Defined in[xterm.d.ts:990](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L990)_

Adds an event listener for when a scroll occurs. The event value is the new position of the viewport.

**`returns`** an `IDisposable` to stop listening.

* * *

### onSelectionChange

• **onSelectionChange** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Defined in[xterm.d.ts:996](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L996)_

Adds an event listener for when a selection change occurs.

**`returns`** an `IDisposable` to stop listening.

* * *

### onTitleChange

• **onTitleChange** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹string›_

_Defined in[xterm.d.ts:1003](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1003)_

Adds an event listener for when an OSC 0 or OSC 2 title change occurs. The event value is the new title.

**`returns`** an `IDisposable` to stop listening.

* * *

### onWriteParsed

• **onWriteParsed** : _[IEvent](https://xtermjs.org/docs/api/terminal/interfaces/ievent/)‹void›_

_Defined in[xterm.d.ts:976](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L976)_

Adds an event listener for when data has been parsed by the terminal, after [write](https://xtermjs.org/docs/api/terminal/classes/terminal/#write) is called. This event is useful to listen for any changes in the buffer.

This fires at most once per frame, after data parsing completes. Note that this can fire when there are still writes pending if there is a lot of data.

* * *

### options

• **options** : _[ITerminalOptions](https://xtermjs.org/docs/api/terminal/interfaces/iterminaloptions/)_

_Defined in[xterm.d.ts:899](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L899)_

Gets or sets the terminal options. This supports setting multiple options.

**`example`** Get a single option
    
    
    console.log(terminal.options.fontSize);
    

**`example`** Set a single option:
    
    
    terminal.options.fontSize = 12;
    

Note that for options that are object, a new object must be used in order to take effect as a reference comparison will be done:
    
    
    const newValue = terminal.options.theme;
    newValue.background = '#000000';
    
    // This won't work
    terminal.options.theme = newValue;
    
    // This will work
    terminal.options.theme = { ...newValue };
    

**`example`** Set multiple options
    
    
    terminal.options = {
      fontSize: 12,
      fontFamily: 'Courier New'
    };
    

* * *

### `Readonly` parser

• **parser** : _[IParser](https://xtermjs.org/docs/api/terminal/interfaces/iparser/)_

_Defined in[xterm.d.ts:852](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L852)_

Get the parser interface to register custom escape sequence handlers.

* * *

### `Readonly` rows

• **rows** : _number_

_Defined in[xterm.d.ts:829](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L829)_

The number of rows in the terminal’s viewport. Use `ITerminalOptions.rows` to set this in the constructor and `Terminal.resize` for when the terminal exists.

* * *

### `Readonly` textarea

• **textarea** : *HTMLTextAreaElement | undefined*  
---|---  
  
_Defined in[xterm.d.ts:822](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L822)_

The textarea that accepts input for the terminal.

* * *

### `Readonly` unicode

• **unicode** : _[IUnicodeHandling](https://xtermjs.org/docs/api/terminal/interfaces/iunicodehandling/)_

_Defined in[xterm.d.ts:858](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L858)_

(EXPERIMENTAL) Get the Unicode handling interface to register and switch Unicode version.

* * *

### `Static` strings

▪ **strings** : _[ILocalizableStrings](https://xtermjs.org/docs/api/terminal/interfaces/ilocalizablestrings/)_

_Defined in[xterm.d.ts:904](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L904)_

Natural language strings that can be localized.

## Methods

### attachCustomKeyEventHandler

▸ **attachCustomKeyEventHandler**(`customKeyEventHandler`: function): _void_

_Defined in[xterm.d.ts:1072](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1072)_

Attaches a custom key event handler which is run before keys are processed, giving consumers of xterm.js ultimate control as to what keys should be processed by the terminal and what keys should not.

**`example`** A custom keymap that overrides the backspace key
    
    
    const keymap = [
      { "key": "Backspace", "shiftKey": false, "mapCode": 8 },
      { "key": "Backspace", "shiftKey": true, "mapCode": 127 }
    ];
    term.attachCustomKeyEventHandler(ev => {
      if (ev.type === 'keydown') {
        for (let i in keymap) {
          if (keymap[i].key == ev.key && keymap[i].shiftKey == ev.shiftKey) {
            socket.send(String.fromCharCode(keymap[i].mapCode));
            return false;
          }
        }
      }
    });
    

**Parameters:**

▪ **customKeyEventHandler** : _function_

The custom KeyboardEvent handler to attach. This is a function that takes a KeyboardEvent, allowing consumers to stop propagation and/or prevent the default action. The function returns whether the event should be processed by xterm.js.

▸ (`event`: KeyboardEvent): _boolean_

**Parameters:**

Name | Type  
---|---  
`event` | KeyboardEvent  
  
**Returns:** _void_

* * *

### attachCustomWheelEventHandler

▸ **attachCustomWheelEventHandler**(`customWheelEventHandler`: function): _void_

_Defined in[xterm.d.ts:1094](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1094)_

Attaches a custom wheel event handler which is run before keys are processed, giving consumers of xterm.js control over whether to proceed or cancel terminal wheel events.

**`example`** A handler that prevents all wheel events while ctrl is held from being processed.
    
    
    term.attachCustomWheelEventHandler(ev => {
      if (ev.ctrlKey) {
        return false;
      }
      return true;
    });
    

**Parameters:**

▪ **customWheelEventHandler** : _function_

The custom WheelEvent handler to attach. This is a function that takes a WheelEvent, allowing consumers to stop propagation and/or prevent the default action. The function returns whether the event should be processed by xterm.js.

▸ (`event`: WheelEvent): _boolean_

**Parameters:**

Name | Type  
---|---  
`event` | WheelEvent  
  
**Returns:** _void_

* * *

### blur

▸ **blur**(): _void_

_Defined in[xterm.d.ts:1008](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1008)_

Unfocus the terminal.

**Returns:** _void_

* * *

### clear

▸ **clear**(): _void_

_Defined in[xterm.d.ts:1238](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1238)_

Clear the entire buffer, making the prompt line the new first line.

**Returns:** _void_

* * *

### clearSelection

▸ **clearSelection**(): _void_

_Defined in[xterm.d.ts:1178](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1178)_

Clears the current terminal selection.

**Returns:** _void_

* * *

### clearTextureAtlas

▸ **clearTextureAtlas**(): _void_

_Defined in[xterm.d.ts:1291](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1291)_

Clears the texture atlas of the webgl renderer if it’s active. Doing this will force a redraw of all glyphs which can workaround issues causing the texture to become corrupt, for example Chromium/Nvidia has an issue where the texture gets messed up when resuming the OS from sleep.

**Returns:** _void_

* * *

### deregisterCharacterJoiner

▸ **deregisterCharacterJoiner**(`joinerId`: number): _void_

_Defined in[xterm.d.ts:1140](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1140)_

(EXPERIMENTAL) Deregisters the character joiner if one was registered. NOTE: character joiners are only used by the webgl renderer.

**Parameters:**

Name | Type | Description  
---|---|---  
`joinerId` | number | The character joiner’s ID (returned after register)  
  
**Returns:** _void_

* * *

### dispose

▸ **dispose**(): _void_

_Implementation of[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

_Defined in[xterm.d.ts:1205](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1205)_

**Returns:** _void_

* * *

### focus

▸ **focus**(): _void_

_Defined in[xterm.d.ts:1013](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1013)_

Focus the terminal.

**Returns:** _void_

* * *

### getSelection

▸ **getSelection**(): _string_

_Defined in[xterm.d.ts:1168](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1168)_

Gets the terminal’s current selection, this is useful for implementing copy behavior outside of xterm.js.

**Returns:** _string_

* * *

### getSelectionPosition

▸ **getSelectionPosition**(): *[IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/) | undefined*  
---|---  
  
_Defined in[xterm.d.ts:1173](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1173)_

Gets the selection position or undefined if there is no selection.

**Returns:** *[IBufferRange](https://xtermjs.org/docs/api/terminal/interfaces/ibufferrange/) | undefined*  
---|---  
  
* * *

### hasSelection

▸ **hasSelection**(): _boolean_

_Defined in[xterm.d.ts:1162](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1162)_

Gets whether the terminal has an active selection.

**Returns:** _boolean_

* * *

### input

▸ **input**(`data`: string, `wasUserInput?`: boolean): _void_

_Defined in[xterm.d.ts:1025](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1025)_

Input data to application side. The data is treated the same way input typed into the terminal would (ie. the [onData](https://xtermjs.org/docs/api/terminal/classes/terminal/#ondata) event will fire).

**Parameters:**

Name | Type | Description  
---|---|---  
`data` | string | The data to forward to the application.  
`wasUserInput?` | boolean | Whether the input is genuine user input. This is true by default and triggers additionalbehavior like focus or selection clearing. Set this to false if the data sent should not be treated like user input would, for example passing an escape sequence to the application.  
  
**Returns:** _void_

* * *

### loadAddon

▸ **loadAddon**(`addon`: [ITerminalAddon](https://xtermjs.org/docs/api/terminal/interfaces/iterminaladdon/)): _void_

_Defined in[xterm.d.ts:1302](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1302)_

Loads an addon into this instance of xterm.js.

**Parameters:**

Name | Type | Description  
---|---|---  
`addon` | [ITerminalAddon](https://xtermjs.org/docs/api/terminal/interfaces/iterminaladdon/) | The addon to load.  
  
**Returns:** _void_

* * *

### open

▸ **open**(`parent`: HTMLElement): _void_

_Defined in[xterm.d.ts:1043](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1043)_

Opens the terminal within an element. This should also be called if the xterm.js element ever changes browser window.

**Parameters:**

Name | Type | Description  
---|---|---  
`parent` | HTMLElement | The element to create the terminal within. This element must be visible (have dimensions) when `open` is called as several DOM- based measurements need to be performed when this function is called.  
  
**Returns:** _void_

* * *

### paste

▸ **paste**(`data`: string): _void_

_Defined in[xterm.d.ts:1275](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1275)_

Writes text to the terminal, performing the necessary transformations for pasted text.

**Parameters:**

Name | Type | Description  
---|---|---  
`data` | string | The text to write to the terminal.  
  
**Returns:** _void_

* * *

### refresh

▸ **refresh**(`start`: number, `end`: number): _void_

_Defined in[xterm.d.ts:1283](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1283)_

Tells the renderer to refresh terminal content between two rows (inclusive) at the next opportunity.

**Parameters:**

Name | Type | Description  
---|---|---  
`start` | number | The row to start from (between 0 and this.rows - 1).  
`end` | number | The row to end at (between start and this.rows - 1).  
  
**Returns:** _void_

* * *

### registerCharacterJoiner

▸ **registerCharacterJoiner**(`handler`: function): _number_

_Defined in[xterm.d.ts:1133](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1133)_

(EXPERIMENTAL) Registers a character joiner, allowing custom sequences of characters to be rendered as a single unit. This is useful in particular for rendering ligatures and graphemes, among other things.

Each registered character joiner is called with a string of text representing a portion of a line in the terminal that can be rendered as a single unit. The joiner must return a sorted array, where each entry is itself an array of length two, containing the start (inclusive) and end (exclusive) index of a substring of the input that should be rendered as a single unit. When multiple joiners are provided, the results of each are collected. If there are any overlapping substrings between them, they are combined into one larger unit that is drawn together.

All character joiners that are registered get called every time a line is rendered in the terminal, so it is essential for the handler function to run as quickly as possible to avoid slowdowns when rendering. Similarly, joiners should strive to return the smallest possible substrings to render together, since they aren’t drawn as optimally as individual characters.

NOTE: character joiners are only used by the webgl renderer.

**Parameters:**

▪ **handler** : _function_

The function that determines character joins. It is called with a string of text that is eligible for joining and returns an array where each entry is an array containing the start (inclusive) and end (exclusive) indexes of ranges that should be rendered as a single unit.

▸ (`text`: string): _[][]_

**Parameters:**

Name | Type  
---|---  
`text` | string  
  
**Returns:** _number_

The ID of the new joiner, this can be used to deregister

* * *

### registerDecoration

▸ **registerDecoration**(`decorationOptions`: [IDecorationOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/)): *[IDecoration](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/) | undefined*  
---|---  
  
_Defined in[xterm.d.ts:1157](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1157)_

(EXPERIMENTAL) Adds a decoration to the terminal using

**`throws`** when options include a negative x offset.

**Parameters:**

Name | Type  
---|---  
`decorationOptions` | [IDecorationOptions](https://xtermjs.org/docs/api/terminal/interfaces/idecorationoptions/)  
**Returns:** *[IDecoration](https://xtermjs.org/docs/api/terminal/interfaces/idecoration/) | undefined*  
---|---  
  
* * *

### registerLinkProvider

▸ **registerLinkProvider**(`linkProvider`: [ILinkProvider](https://xtermjs.org/docs/api/terminal/interfaces/ilinkprovider/)): _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

_Defined in[xterm.d.ts:1102](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1102)_

Registers a link provider, allowing a custom parser to be used to match and handle links. Multiple link providers can be used, they will be asked in the order in which they are registered.

**Parameters:**

Name | Type | Description  
---|---|---  
`linkProvider` | [ILinkProvider](https://xtermjs.org/docs/api/terminal/interfaces/ilinkprovider/) | The link provider to use to detect links.  
  
**Returns:** _[IDisposable](https://xtermjs.org/docs/api/terminal/interfaces/idisposable/)_

* * *

### registerMarker

▸ **registerMarker**(`cursorYOffset?`: number): _[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/)_

_Defined in[xterm.d.ts:1147](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1147)_

Adds a marker to the normal buffer and returns it.

**Parameters:**

Name | Type | Description  
---|---|---  
`cursorYOffset?` | number | The y position offset of the marker from the cursor.  
  
**Returns:** _[IMarker](https://xtermjs.org/docs/api/terminal/interfaces/imarker/)_

The new marker or undefined.

* * *

### reset

▸ **reset**(): _void_

_Defined in[xterm.d.ts:1296](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1296)_

Perform a full reset (RIS, aka ‘\x1bc’).

**Returns:** _void_

* * *

### resize

▸ **resize**(`columns`: number, `rows`: number): _void_

_Defined in[xterm.d.ts:1034](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1034)_

Resizes the terminal. It’s best practice to debounce calls to resize, this will help ensure that the pty can respond to the resize event before another one occurs.

**Parameters:**

Name | Type  
---|---  
`columns` | number  
`rows` | number  
  
**Returns:** _void_

* * *

### scrollLines

▸ **scrollLines**(`amount`: number): _void_

_Defined in[xterm.d.ts:1211](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1211)_

Scroll the display of the terminal

**Parameters:**

Name | Type | Description  
---|---|---  
`amount` | number | The number of lines to scroll down (negative scroll up).  
  
**Returns:** _void_

* * *

### scrollPages

▸ **scrollPages**(`pageCount`: number): _void_

_Defined in[xterm.d.ts:1217](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1217)_

Scroll the display of the terminal by a number of pages.

**Parameters:**

Name | Type | Description  
---|---|---  
`pageCount` | number | The number of pages to scroll (negative scrolls up).  
  
**Returns:** _void_

* * *

### scrollToBottom

▸ **scrollToBottom**(): _void_

_Defined in[xterm.d.ts:1227](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1227)_

Scrolls the display of the terminal to the bottom.

**Returns:** _void_

* * *

### scrollToLine

▸ **scrollToLine**(`line`: number): _void_

_Defined in[xterm.d.ts:1233](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1233)_

Scrolls to a line within the buffer.

**Parameters:**

Name | Type | Description  
---|---|---  
`line` | number | The 0-based line index to scroll to.  
  
**Returns:** _void_

* * *

### scrollToTop

▸ **scrollToTop**(): _void_

_Defined in[xterm.d.ts:1222](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1222)_

Scrolls the display of the terminal to the top.

**Returns:** _void_

* * *

### select

▸ **select**(`column`: number, `row`: number, `length`: number): _void_

_Defined in[xterm.d.ts:1186](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1186)_

Selects text within the terminal.

**Parameters:**

Name | Type | Description  
---|---|---  
`column` | number | The column the selection starts at.  
`row` | number | The row the selection starts at.  
`length` | number | The length of the selection.  
  
**Returns:** _void_

* * *

### selectAll

▸ **selectAll**(): _void_

_Defined in[xterm.d.ts:1191](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1191)_

Selects all text within the terminal.

**Returns:** _void_

* * *

### selectLines

▸ **selectLines**(`start`: number, `end`: number): _void_

_Defined in[xterm.d.ts:1198](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1198)_

Selects text in the buffer between 2 lines.

**Parameters:**

Name | Type | Description  
---|---|---  
`start` | number | The 0-based line index to select from (inclusive).  
`end` | number | The 0-based line index to select to (inclusive).  
  
**Returns:** _void_

* * *

### write

▸ **write**(`data`: string | Uint8Array, `callback?`: function): _void_  
---|---  
  
_Defined in[xterm.d.ts:1253](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1253)_

Write data to the terminal.

Note that the change will not be reflected in the [buffer](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-buffer) immediately as the data is processed asynchronously. Provide a {@link callback} to know when the data was processed.

**Parameters:**

▪ **data** : *string | Uint8Array*  
---|---  
  
The data to write to the terminal. This can either be raw bytes given as Uint8Array from the pty or a string. Raw bytes will always be treated as UTF-8 encoded, string data as UTF-16.

▪`Optional` **callback** : _function_

Optional callback that fires when the data was processed by the parser. This callback must be provided and awaited in order for [buffer](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-buffer) to reflect the change in the write.

▸ (): _void_

**Returns:** _void_

* * *

### writeln

▸ **writeln**(`data`: string | Uint8Array, `callback?`: function): _void_  
---|---  
  
_Defined in[xterm.d.ts:1268](https://github.com/xtermjs/xterm.js/blob/6.0.0/typings/xterm.d.ts#L1268)_

Writes data to the terminal, followed by a break line character (\n).

Note that the change will not be reflected in the [buffer](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-buffer) immediately as the data is processed asynchronously. Provide a {@link callback} to know when the data was processed.

**Parameters:**

▪ **data** : *string | Uint8Array*  
---|---  
  
The data to write to the terminal. This can either be raw bytes given as Uint8Array from the pty or a string. Raw bytes will always be treated as UTF-8 encoded, string data as UTF-16.

▪`Optional` **callback** : _function_

Optional callback that fires when the data was processed by the parser. This callback must be provided and awaited in order for [buffer](https://xtermjs.org/docs/api/terminal/classes/terminal/#readonly-buffer) to reflect the change in the write.

▸ (): _void_

**Returns:** _void_