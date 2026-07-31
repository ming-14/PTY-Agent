(function () {
  'use strict';

  const TICK_MS = 40;
  const SPRITE_W = 128;
  const SPRITE_COLS = 8;

  class Pose {
    constructor(spriteIdx, cx, cy, vx, vy, duration) {
      this.spriteIdx = spriteIdx;
      this.cx = cx;
      this.cy = cy;
      this.vx = vx;
      this.vy = vy;
      this.duration = duration;
    }
  }

  class Animation {
    constructor(poses) {
      this.poses = poses;
      this.totalDuration = poses.reduce((s, p) => s + p.duration, 0);
    }
  }

  const ACTIONS = {};

  function p(idx, cx, cy, vx, vy, dur) {
    return new Pose(idx, cx, cy, vx, vy, dur);
  }

  function a(name, poses) {
    ACTIONS[name] = new Animation(poses);
  }

  a('立つ', [p(0, 64, 128, 0, 0, 250)]);
  a('歩く', [
    p(0, 64, 128, -2, 0, 6),
    p(1, 64, 128, -2, 0, 6),
    p(0, 64, 128, -2, 0, 6),
    p(2, 64, 128, -2, 0, 6),
  ]);
  a('走る', [
    p(0, 64, 128, -4, 0, 2),
    p(1, 64, 128, -4, 0, 2),
    p(0, 64, 128, -4, 0, 2),
    p(2, 64, 128, -4, 0, 2),
  ]);
  a('猛ダッシュ', [
    p(0, 64, 128, -8, 0, 2),
    p(1, 64, 128, -8, 0, 2),
    p(0, 64, 128, -8, 0, 2),
    p(2, 64, 128, -8, 0, 2),
  ]);
  a('座る', [p(10, 64, 128, 0, 0, 250)]);
  a('座って見上げる', [p(25, 64, 128, 0, 0, 250)]);
  a('座って首が回る', [
    p(25, 64, 128, 0, 0, 5),
    p(14, 64, 128, 0, 0, 5),
    p(26, 64, 128, 0, 0, 5),
    p(15, 64, 128, 0, 0, 5),
    p(27, 64, 128, 0, 0, 5),
    p(16, 64, 128, 0, 0, 5),
    p(28, 64, 128, 0, 0, 5),
    p(10, 64, 128, 0, 0, 5),
  ]);
  a('楽に座る', [p(29, 64, 112, 0, 0, 250)]);
  a('足を下ろして座る', [p(30, 64, 112, 0, 0, 250)]);
  a('足をぶらぶらさせる', [
    p(30, 64, 112, 0, 0, 5),
    p(31, 64, 112, 0, 0, 15),
    p(30, 64, 112, 0, 0, 5),
    p(32, 64, 112, 0, 0, 15),
  ]);
  a('寝そべる', [p(20, 64, 128, 0, 0, 250)]);
  a('ずりずり', [
    p(19, 64, 128, 0, 0, 28),
    p(19, 64, 128, -2, 0, 4),
    p(20, 64, 128, -2, 0, 4),
    p(20, 64, 128, -1, 0, 4),
    p(20, 64, 128, 0, 0, 24),
  ]);
  a('天井に掴まる', [p(22, 64, 48, 0, 0, 250)]);
  a('天井を伝う', [
    p(24, 64, 48, 0, 0, 16),
    p(24, 64, 48, -1, 0, 4),
    p(22, 64, 48, -1, 0, 4),
    p(23, 64, 48, -1, 0, 4),
    p(23, 64, 48, 0, 0, 16),
    p(23, 64, 48, -2, 0, 4),
    p(22, 64, 48, -2, 0, 4),
    p(24, 64, 48, -2, 0, 4),
  ]);
  a('壁に掴まる', [p(12, 64, 128, 0, 0, 250)]);
  a('壁を登る_上', [
    p(13, 64, 128, 0, 0, 16),
    p(13, 64, 128, 0, -1, 4),
    p(11, 64, 128, 0, -1, 4),
    p(12, 64, 128, 0, -1, 4),
    p(12, 64, 128, 0, 0, 16),
    p(12, 64, 128, 0, -2, 4),
    p(11, 64, 128, 0, -2, 4),
    p(13, 64, 128, 0, -2, 4),
  ]);
  a('壁を登る_下', [
    p(13, 64, 128, 0, 0, 16),
    p(13, 64, 128, 0, 2, 4),
    p(11, 64, 128, 0, 2, 4),
    p(12, 64, 128, 0, 2, 4),
    p(12, 64, 128, 0, 0, 16),
    p(12, 64, 128, 0, 1, 4),
    p(11, 64, 128, 0, 1, 4),
    p(13, 64, 128, 0, 1, 4),
  ]);
  a('落ちる', [p(3, 64, 128, 0, 0, 250)]);
  a('跳ねる', [
    p(17, 64, 128, 0, 0, 4),
    p(18, 64, 128, 0, 0, 4),
  ]);
  a('転ぶ', [
    p(18, 64, 128, -8, 0, 8),
    p(17, 64, 128, -4, 0, 4),
    p(19, 64, 128, -2, 0, 4),
    p(19, 64, 128, 0, 0, 10),
    p(18, 64, 104, -4, 0, 4),
  ]);
  a('つままれる_左大', [p(8, 64, 128, 0, 0, 5)]);
  a('つままれる_左小', [p(6, 64, 128, 0, 0, 5)]);
  a('つままれる_中', [p(0, 64, 128, 0, 0, 5)]);
  a('つままれる_右小', [p(7, 64, 128, 0, 0, 5)]);
  a('つままれる_右大', [p(9, 64, 128, 0, 0, 5)]);
  a('抵抗する', [
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(0, 64, 128, 0, 0, 50),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(0, 64, 128, 0, 0, 100),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 5),
    p(5, 64, 128, 0, 0, 5),
    p(4, 64, 128, 0, 0, 2),
    p(5, 64, 128, 0, 0, 2),
    p(4, 64, 128, 0, 0, 2),
    p(5, 64, 128, 0, 0, 2),
    p(4, 64, 128, 0, 0, 2),
    p(5, 64, 128, 0, 0, 2),
    p(4, 64, 128, 0, 0, 2),
    p(5, 64, 128, 0, 0, 2),
  ]);
  a('ジャンプ', [p(21, 64, 128, 0, 0, 250)]);

  function spritePos(idx) {
    const col = idx % SPRITE_COLS;
    const row = Math.floor(idx / SPRITE_COLS);
    return { x: -col * SPRITE_W, y: -row * SPRITE_W };
  }

  class Environment {
    constructor() { this.update(); }
    update() {
      this.left = 0;
      this.top = 0;
      this.right = window.innerWidth;
      this.bottom = window.innerHeight;
      this.width = window.innerWidth;
      this.height = window.innerHeight;
    }
    isFloor(y) { return y >= this.bottom - 1; }
    isCeiling(y) { return y <= this.top + 1; }
    isLeftWall(x) { return x <= this.left + 1; }
    isRightWall(x) { return x >= this.right - 1; }
  }

  class Mascot {
    constructor(manager) {
      this.manager = manager;
      this.x = 0;
      this.y = 0;
      this.lookRight = Math.random() < 0.5;
      this.env = new Environment();

      this.behaviorName = null;
      this.steps = [];
      this.stepIdx = 0;

      this.poseIdx = 0;
      this.poseTick = 0;
      this.stepDuration = 0;
      this.stepTick = 0;

      this.moveTargetX = null;
      this.climbTargetY = null;

      this.fallVx = 0;
      this.fallVy = 0;
      this.fallModX = 0;
      this.fallModY = 0;

      this.dragging = false;
      this.dragOffsetX = 0;
      this.dragOffsetY = 0;
      this.cursorX = 0;
      this.cursorY = 0;
      this.cursorDx = 0;
      this.cursorDy = 0;
      this.prevCursorX = 0;
      this.prevCursorY = 0;

      this.currentSprite = 0;
      this.currentCx = 64;
      this.currentCy = 128;

      this.alive = true;

      this.el = document.createElement('div');
      this.el.className = 'shimeji-mascot';
      document.body.appendChild(this.el);

      this.el.addEventListener('mousedown', e => this._onMouseDown(e));
      this.el.addEventListener('touchstart', e => this._onTouchStart(e), { passive: false });
    }

    _onMouseDown(e) {
      e.preventDefault();
      e.stopPropagation();
      this._startDrag(e.clientX, e.clientY);
      const onMove = ev => {
        // 终端可能吞掉 mouseup（VT 鼠标模式），通过 buttons 检测按钮是否已松开
        if (ev.buttons === 0) {
          this._endDrag();
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          return;
        }
        this._onDragMove(ev.clientX, ev.clientY);
      };
      const onUp = ev => {
        this._endDrag();
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    }

    _onTouchStart(e) {
      e.preventDefault();
      e.stopPropagation();
      const t = e.touches[0];
      this._startDrag(t.clientX, t.clientY);
      const onMove = ev => {
        const tt = ev.touches[0];
        this._onDragMove(tt.clientX, tt.clientY);
      };
      const onEnd = () => {
        this._endDrag();
        document.removeEventListener('touchmove', onMove);
        document.removeEventListener('touchend', onEnd);
      };
      document.addEventListener('touchmove', onMove, { passive: false });
      document.addEventListener('touchend', onEnd);
    }

    _startDrag(cx, cy) {
      this.dragging = true;
      this.dragOffsetX = this.x - cx;
      this.dragOffsetY = this.y - cy;
      this.prevCursorX = cx;
      this.prevCursorY = cy;
      this.cursorX = cx;
      this.cursorY = cy;
    }

    _onDragMove(cx, cy) {
      if (!this.dragging) return;
      this.cursorDx = cx - this.prevCursorX;
      this.cursorDy = cy - this.prevCursorY;
      this.prevCursorX = cx;
      this.prevCursorY = cy;
      this.cursorX = cx;
      this.cursorY = cy;
      this.x = cx + this.dragOffsetX;
      this.y = cy + this.dragOffsetY;
    }

    _endDrag() {
      if (!this.dragging) return;
      this.dragging = false;
      this.setBehavior('投げられる');
    }

    isOnFloor() { return this.env.isFloor(this.y); }
    isOnCeiling() { return this.env.isCeiling(this.y); }
    isOnLeftWall() { return this.env.isLeftWall(this.x); }
    isOnRightWall() { return this.env.isRightWall(this.x); }
    isOnWall() { return this.lookRight ? this.isOnRightWall() : this.isOnLeftWall(); }

    setBehavior(name) {
      const def = BEHAVIORS[name];
      if (!def) return;
      this.behaviorName = name;
      this.steps = def.steps.slice();
      this.stepIdx = 0;
      this._initStep();
    }

    _initStep() {
      if (this.stepIdx >= this.steps.length) {
        this._finishBehavior();
        return;
      }
      const step = this.steps[this.stepIdx];
      this.poseIdx = 0;
      this.poseTick = 0;
      this.stepTick = 0;
      this.moveTargetX = null;
      this.climbTargetY = null;

      switch (step.type) {
        case 'action': {
          const anim = ACTIONS[step.name];
          if (!anim) { this._nextStep(); return; }
          this.stepDuration = typeof step.duration === 'function' ? step.duration() : (step.duration != null ? step.duration : anim.totalDuration);
          break;
        }
        case 'move': {
          const anim = ACTIONS[step.name];
          if (!anim) { this._nextStep(); return; }
          this.moveTargetX = typeof step.targetX === 'function' ? step.targetX(this) : step.targetX;
          this.stepDuration = 999999;
          break;
        }
        case 'climb': {
          const anim = ACTIONS[step.name];
          if (!anim) { this._nextStep(); return; }
          this.climbTargetY = typeof step.targetY === 'function' ? step.targetY(this) : step.targetY;
          this.stepDuration = 999999;
          break;
        }
        case 'fall': {
          this.fallVx = step.useCursorVelocity ? this.cursorDx * 2 : 0;
          this.fallVy = step.useCursorVelocity ? this.cursorDy * 2 : 0;
          this.fallModX = 0;
          this.fallModY = 0;
          break;
        }
        case 'look': {
          if (step.right != null) {
            this.lookRight = step.right;
          } else {
            this.lookRight = !this.lookRight;
          }
          this._nextStep();
          break;
        }
        case 'offset': {
          const dx = typeof step.dx === 'function' ? step.dx(this) : (step.dx || 0);
          const dy = typeof step.dy === 'function' ? step.dy(this) : (step.dy || 0);
          this.x += dx;
          this.y += dy;
          this._nextStep();
          break;
        }
        case 'breed': {
          this.manager.breed(this);
          this._nextStep();
          break;
        }
        case 'select':
        case 'dragged':
          break;
      }
    }

    _nextStep() {
      this.stepIdx++;
      this._initStep();
    }

    _finishBehavior() {
      const def = BEHAVIORS[this.behaviorName];
      if (def && def.next && def.next.length > 0) {
        this.setBehavior(this._weightedRandom(def.next));
      } else {
        this._chooseRandomBehavior();
      }
    }

    _chooseRandomBehavior() {
      this.env.update();
      const c = [];

      if (this.isOnFloor()) {
        c.push({ name: '立ってボーっとする', freq: 200 });
        c.push({ name: '座ってボーっとする', freq: 100 });
        c.push({ name: 'ワークエリアの下辺を歩く', freq: 200 });
        c.push({ name: 'ワークエリアの下辺でずりずり', freq: 10 });
        c.push({ name: 'ワークエリアの下辺の左の端っこで座る', freq: 60 });
        c.push({ name: 'ワークエリアの下辺の右の端っこで座る', freq: 60 });
        c.push({ name: 'ワークエリアの下辺を走る', freq: 30 });
        c.push({ name: '走ってワークエリアの下辺の左の端っこで座る', freq: 20 });
        c.push({ name: '走ってワークエリアの下辺の右の端っこで座る', freq: 20 });
      }

      if (this.isOnWall()) {
        c.push({ name: '壁に掴まってボーっとする', freq: 100 });
        if (!this.isOnFloor()) {
          c.push({ name: '壁から落ちる', freq: 100 });
        }
        c.push({ name: 'ワークエリアの壁を途中まで登る', freq: 50 });
      }

      if (this.isOnCeiling()) {
        c.push({ name: '天井に掴まってボーっとする', freq: 100 });
        c.push({ name: '天井から落ちる', freq: 10 });
        c.push({ name: 'ワークエリアの上辺を伝う', freq: 100 });
      }

      if (c.length === 0) {
        c.push({ name: '落下する', freq: 1 });
      }

      this.setBehavior(this._weightedRandom(c));
    }

    _weightedRandom(list) {
      const totalFreq = list.reduce((s, c) => s + c.freq, 0);
      let r = Math.random() * totalFreq;
      for (const c of list) {
        r -= c.freq;
        if (r <= 0) return c.name;
      }
      return list[list.length - 1].name;
    }

    tick() {
      if (!this.alive) return;

      if (this.dragging) {
        this._tickDraggedAnim();
        this._render();
        return;
      }

      this.env.update();

      // 环境变化后（如窗口大小变化），检查位置是否仍在合理表面
      if (!this.isOnFloor() && !this.isOnWall() && !this.isOnCeiling()) {
        // 如果精灵完全在视口外，传送到顶部上方自然掉落回来
        const spriteLeft = this.x - this.currentCx;
        const spriteTop = this.y - this.currentCy;
        if (spriteLeft + SPRITE_W <= 0 || spriteLeft >= this.env.width ||
            this.y <= 0 || spriteTop >= this.env.height) {
          this.x = this.env.width / 2;
          this.y = -SPRITE_W;
          this.fallVx = 0;
          this.fallVy = 0;
        }
        if (this.behaviorName !== '落下する' && this.behaviorName !== '投げられる') {
          this.setBehavior('落下する');
          this._render();
          return;
        }
      }

      if (this.stepIdx >= this.steps.length) {
        this._finishBehavior();
        return;
      }

      const step = this.steps[this.stepIdx];

      switch (step.type) {
        case 'action': this._tickAction(step); break;
        case 'move': this._tickMove(step); break;
        case 'climb': this._tickClimb(step); break;
        case 'fall': this._tickFall(); break;
        case 'dragged': this._tickDraggedAnim(); break;
        case 'select': this._tickSelect(step); break;
      }

      this._render();
    }

    _tickAction(step) {
      const anim = ACTIONS[step.name];
      if (!anim) { this._nextStep(); return; }

      if (this.stepTick >= this.stepDuration) {
        this._nextStep();
        return;
      }

      const pose = anim.poses[this.poseIdx];
      if (!pose) { this._nextStep(); return; }

      this.currentSprite = pose.spriteIdx;
      this.currentCx = pose.cx;
      this.currentCy = pose.cy;

      const vx = this.lookRight ? -pose.vx : pose.vx;
      this.x += vx;
      this.y += pose.vy;

      this.poseTick++;
      this.stepTick++;

      if (this.poseTick >= pose.duration) {
        this.poseTick = 0;
        this.poseIdx++;
        if (this.poseIdx >= anim.poses.length) {
          this.poseIdx = 0;
        }
      }
    }

    _tickMove(step) {
      const anim = ACTIONS[step.name];
      if (!anim) { this._nextStep(); return; }

      if (this.moveTargetX != null) {
        const dist = this.moveTargetX - this.x;
        if (Math.abs(dist) < 4) {
          this._nextStep();
          return;
        }
        this.lookRight = dist > 0;
      }

      const pose = anim.poses[this.poseIdx];
      if (!pose) { this._nextStep(); return; }

      this.currentSprite = pose.spriteIdx;
      this.currentCx = pose.cx;
      this.currentCy = pose.cy;

      const vx = this.lookRight ? -pose.vx : pose.vx;
      this.x += vx;
      this.y += pose.vy;

      this.poseTick++;
      if (this.poseTick >= pose.duration) {
        this.poseTick = 0;
        this.poseIdx++;
        if (this.poseIdx >= anim.poses.length) {
          this.poseIdx = 0;
        }
      }

      if (this.x < this.env.left) this.x = this.env.left;
      if (this.x > this.env.right) this.x = this.env.right;
    }

    _tickClimb(step) {
      const anim = ACTIONS[step.name];
      if (!anim) { this._nextStep(); return; }

      if (this.climbTargetY != null) {
        if (this.y <= this.climbTargetY + 2) {
          this._nextStep();
          return;
        }
      }

      const pose = anim.poses[this.poseIdx];
      if (!pose) { this._nextStep(); return; }

      this.currentSprite = pose.spriteIdx;
      this.currentCx = pose.cx;
      this.currentCy = pose.cy;

      this.x += pose.vx;
      this.y += pose.vy;

      this.poseTick++;
      if (this.poseTick >= pose.duration) {
        this.poseTick = 0;
        this.poseIdx++;
        if (this.poseIdx >= anim.poses.length) {
          this.poseIdx = 0;
        }
      }
    }

    _tickFall() {
      const anim = ACTIONS['落ちる'];
      const pose = anim.poses[0];
      this.currentSprite = pose.spriteIdx;
      this.currentCx = pose.cx;
      this.currentCy = pose.cy;

      const gravity = 2;
      const airResistX = 0.05;
      const airResistY = 0.1;

      if (this.fallVx !== 0) {
        this.lookRight = this.fallVx > 0;
      }

      this.fallVx -= this.fallVx * airResistX;
      this.fallVy = this.fallVy - this.fallVy * airResistY + gravity;

      this.fallModX += this.fallVx % 1;
      this.fallModY += this.fallVy % 1;
      const dx = Math.floor(this.fallVx) + Math.floor(this.fallModX);
      const dy = Math.floor(this.fallVy) + Math.floor(this.fallModY);
      this.fallModX %= 1;
      this.fallModY %= 1;

      this.x += dx;
      this.y += dy;

      if (this.y >= this.env.bottom) {
        this.y = this.env.bottom;
        this._nextStep();
      } else if (this.isOnWall()) {
        this._nextStep();
      }

      if (this.x < this.env.left) this.x = this.env.left;
      if (this.x > this.env.right) this.x = this.env.right;
    }

    _tickDraggedAnim() {
      const dx = this.cursorDx;
      let animName;
      if (dx < -4) animName = 'つままれる_左大';
      else if (dx < -2) animName = 'つままれる_左小';
      else if (dx < 2) animName = 'つままれる_中';
      else if (dx < 4) animName = 'つままれる_右小';
      else animName = 'つままれる_右大';

      const anim = ACTIONS[animName];
      if (anim && anim.poses[0]) {
        this.currentSprite = anim.poses[0].spriteIdx;
        this.currentCx = anim.poses[0].cx;
        this.currentCy = anim.poses[0].cy;
      }
      this.cursorDx = 0;
      this.cursorDy = 0;
    }

    _tickSelect(step) {
      for (const opt of step.options) {
        if (opt.type === 'sequence') {
          if (!opt.condition || opt.condition(this)) {
            const before = this.steps.slice(0, this.stepIdx);
            const after = this.steps.slice(this.stepIdx + 1);
            this.steps = [...before, ...opt.steps, ...after];
            this._initStep();
            return;
          }
        }
      }
      this._nextStep();
    }

    _render() {
      const pos = spritePos(this.currentSprite);
      this.el.style.backgroundPosition = pos.x + 'px ' + pos.y + 'px';
      const displayX = this.x - this.currentCx;
      const displayY = this.y - this.currentCy;
      if (this.lookRight) {
        this.el.style.transform = `translate(${displayX}px, ${displayY}px) scaleX(-1)`;
      } else {
        this.el.style.transform = `translate(${displayX}px, ${displayY}px)`;
      }
    }

    dispose() {
      this.alive = false;
      if (this.el.parentNode) this.el.parentNode.removeChild(this.el);
    }
  }

  const BEHAVIORS = {};

  function b(name, steps, opts) {
    BEHAVIORS[name] = { steps, ...(opts || {}) };
  }

  b('落下する', [
    { type: 'fall' },
    { type: 'select', options: [
      { type: 'sequence', condition: m => m.isOnFloor(), steps: [
        { type: 'action', name: '跳ねる' },
        { type: 'action', name: '立つ', duration: () => 100 + Math.random() * 100 },
      ]},
      { type: 'sequence', condition: () => true, steps: [
        { type: 'action', name: '壁に掴まる', duration: 100 },
      ]},
    ]},
  ]);

  b('ドラッグされる', [
    { type: 'dragged' },
    { type: 'action', name: '抵抗する' },
  ], { loop: true });

  b('投げられる', [
    { type: 'fall', useCursorVelocity: true },
    { type: 'select', options: [
      { type: 'sequence', condition: m => m.isOnFloor(), steps: [
        { type: 'action', name: '跳ねる' },
        { type: 'action', name: '立つ', duration: () => 100 + Math.random() * 100 },
      ]},
      { type: 'sequence', condition: () => true, steps: [
        { type: 'action', name: '壁に掴まる', duration: 100 },
      ]},
    ]},
  ]);

  b('立ってボーっとする', [
    { type: 'action', name: '立つ', duration: () => 500 + Math.random() * 1000 },
  ]);

  b('座ってボーっとする', [
    { type: 'action', name: '座る', duration: () => 500 + Math.random() * 1000 },
  ], { next: [
    { name: '座って足をぶらぶらさせる', freq: 100 },
    { name: '寝そべってボーっとする', freq: 100 },
  ]});

  b('寝そべってボーっとする', [
    { type: 'action', name: '寝そべる', duration: () => 500 + Math.random() * 1000 },
  ], { next: [
    { name: '座ってボーっとする', freq: 100 },
    { name: 'ワークエリアの下辺でずりずり', freq: 100 },
  ]});

  b('座って足をぶらぶらさせる', [
    { type: 'action', name: '楽に座る', duration: 10 },
    { type: 'action', name: '足を下ろして座る', duration: () => 100 + Math.random() * 100 },
    { type: 'action', name: '足をぶらぶらさせる', duration: () => 500 + Math.random() * 100 },
    { type: 'action', name: '足を下ろして座る', duration: () => 100 + Math.random() * 100 },
    { type: 'action', name: '楽に座る', duration: 10 },
  ]);

  b('壁に掴まってボーっとする', [
    { type: 'action', name: '壁に掴まる', duration: () => 500 + Math.random() * 1000 },
  ]);

  b('壁から落ちる', [
    { type: 'offset', dx: m => m.lookRight ? -1 : 1, dy: 0 },
    { type: 'action', name: '立つ' },
  ]);

  b('天井に掴まってボーっとする', [
    { type: 'action', name: '天井に掴まる', duration: () => 500 + Math.random() * 1000 },
  ]);

  b('天井から落ちる', [
    { type: 'offset', dx: 0, dy: 1 },
    { type: 'action', name: '立つ' },
  ]);

  b('ワークエリアの下辺を歩く', [
    { type: 'move', name: '歩く', targetX: m => m.env.left + 64 + Math.random() * (m.env.width - 128) },
  ]);

  b('ワークエリアの下辺を走る', [
    { type: 'move', name: '走る', targetX: m => m.env.left + 64 + Math.random() * (m.env.width - 128) },
  ]);

  b('ワークエリアの下辺でずりずり', [
    { type: 'move', name: 'ずりずり', targetX: m => m.env.left + 64 + Math.random() * (m.env.width - 128) },
  ], { next: [
    { name: '寝そべってボーっとする', freq: 1 },
  ]});

  b('ワークエリアの下辺の左の端っこで座る', [
    { type: 'move', name: '歩く', targetX: m => m.env.left + 100 + Math.random() * 300 },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'look', right: true },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'action', name: '座る', duration: () => 500 + Math.random() * 1000 },
  ]);

  b('ワークエリアの下辺の右の端っこで座る', [
    { type: 'move', name: '歩く', targetX: m => m.env.right - 100 - Math.random() * 300 },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'look', right: false },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'action', name: '座る', duration: () => 500 + Math.random() * 1000 },
  ]);

  b('ワークエリアの下辺から左の壁によじのぼる', [
    { type: 'move', name: '歩く', targetX: m => m.env.left },
    { type: 'climb', name: '壁を登る_上', targetY: m => m.env.bottom - 64 },
  ]);

  b('ワークエリアの下辺から右の壁によじのぼる', [
    { type: 'move', name: '歩く', targetX: m => m.env.right },
    { type: 'climb', name: '壁を登る_上', targetY: m => m.env.bottom - 64 },
  ]);

  b('走ってワークエリアの下辺の左の端っこで座る', [
    { type: 'move', name: '走る', targetX: m => m.env.left + 100 + Math.random() * 300 },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'look', right: true },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'action', name: '座る', duration: () => 500 + Math.random() * 1000 },
  ]);

  b('走ってワークエリアの下辺の右の端っこで座る', [
    { type: 'move', name: '走る', targetX: m => m.env.right - 100 - Math.random() * 300 },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'look', right: false },
    { type: 'action', name: '立つ', duration: () => 20 + Math.random() * 20 },
    { type: 'action', name: '座る', duration: () => 500 + Math.random() * 1000 },
  ]);

  b('ワークエリアの壁を途中まで登る', [
    { type: 'climb', name: '壁を登る_上', targetY: m => m.env.top + 64 + Math.random() * (m.env.height - 128) },
  ]);

  b('ワークエリアの壁を登る', [
    { type: 'climb', name: '壁を登る_上', targetY: m => m.env.top + 64 },
    { type: 'offset', dx: 0, dy: -64 },
    { type: 'look' },
    { type: 'move', name: '天井を伝う', targetX: m => m.lookRight ? m.env.left + Math.random() * 100 : m.env.right - Math.random() * 100 },
  ]);

  b('ワークエリアの上辺を伝う', [
    { type: 'move', name: '天井を伝う', targetX: m => m.env.left + 64 + Math.random() * (m.env.width - 128) },
  ]);

  b('マウスの周りに集まる', [
    { type: 'action', name: '座って見上げる', duration: 250 },
  ]);

  b('分裂する', [
    { type: 'breed' },
  ]);

  class ShimejiManager {
    constructor(opts) {
      this.mascots = [];
      this.maxCount = (opts && opts.maxCount) || 5;
      this.running = false;
      this.intervalId = null;
    }

    start() {
      if (this.running) return;
      this.running = true;
      this.intervalId = setInterval(() => this._tick(), TICK_MS);
    }

    stop() {
      this.running = false;
      if (this.intervalId) {
        clearInterval(this.intervalId);
        this.intervalId = null;
      }
    }

    addMascot() {
      if (this.mascots.length >= this.maxCount) return;
      const m = new Mascot(this);
      m.x = 64 + Math.random() * (window.innerWidth - 128);
      m.y = window.innerHeight;
      m.env.update();
      m.setBehavior('立ってボーっとする');
      m._render();
      this.mascots.push(m);
    }

    removeMascot(mascot) {
      const idx = this.mascots.indexOf(mascot);
      if (idx >= 0) {
        this.mascots.splice(idx, 1);
        mascot.dispose();
      }
    }

    breed(parent) {
      if (this.mascots.length >= this.maxCount) return;
      const m = new Mascot(this);
      m.x = parent.x + (Math.random() < 0.5 ? -30 : 30);
      m.y = parent.y;
      m.lookRight = Math.random() < 0.5;
      m.env.update();
      m.setBehavior('立ってボーっとする');
      this.mascots.push(m);
    }

    gatherAll() {
      for (const m of this.mascots) {
        m.setBehavior('マウスの周りに集まる');
      }
    }

    remainOne() {
      while (this.mascots.length > 1) {
        this.mascots[this.mascots.length - 1].dispose();
        this.mascots.pop();
      }
    }

    disposeAll() {
      for (const m of this.mascots) m.dispose();
      this.mascots = [];
    }

    _tick() {
      for (const m of this.mascots) {
        m.tick();
      }
    }
  }

  window.Shimeji = {
    create: function (opts) {
      return new ShimejiManager(opts);
    }
  };

})();
