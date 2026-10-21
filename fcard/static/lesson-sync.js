// Only final ratings/cursor cross the network. Raw answers and automatic verdicts
// remain in the view's memory. One in-flight write, optimistic revisions, durable outbox.
class LessonSync {
  static same(a, b) {
    return a.cursor === b.cursor && a.ratings.length === b.ratings.length
      && a.ratings.every((r, i) => r.rating === b.ratings[i].rating && r.control === b.ratings[i].control);
  }
  constructor(data, { request, storage, key, status, error, complete }) {
    Object.assign(this, { request, storage, key, status, error, complete });
    this.revision = data.revision;
    this.state = data.state;
    this.completed = data.completed_at !== null;
    this.generation = this.saved = 0;
    this.retry = null;
    this.flight = null;
    this.blocked = false;
    this.ending = false;
    this.delay = 2000;
    if (this.completed) { this.clear(); return; }
    try {
      const local = JSON.parse(storage.getItem(key) || 'null');
      if (local && Number.isInteger(local.state?.cursor) && local.state.cursor >= 0 && local.state.cursor < 20
          && Array.isArray(local.state.ratings) && [local.state.cursor, local.state.cursor + 1].includes(local.state.ratings.length)) {
        const same = LessonSync.same;
        if (!same(local.state, data.state)) {
          if (local.revision === data.revision || (local.sent && same(local.sent.payload.state, data.state))) {
            this.state = local.state;
            this.generation++;
          } else {
            this.fail('Saved and local progress differ. Choose the saved state or keep this tab to recover local changes.', 409);
          }
        }
        // If no acknowledgement reached this tab, retry the exact old request first.
        if (local.sent && local.revision === data.revision) {
          this.retry = local.sent;
          if (!this.generation) this.generation++;
        }
      }
    } catch (_) { this.localUnavailable = true; }
  }
  clear() { try { this.storage.removeItem(this.key); } catch (_) {} }
  persist() {
    try { this.storage.setItem(this.key, JSON.stringify({ revision: this.revision, state: this.state, sent: this.retry })); }
    catch (_) { this.localUnavailable = true; }
  }
  fail(message, code) {
    this.blocked = true;
    this.status('Progress has not been fully synced. Local pending changes are kept.');
    this.error(message, code);
  }
  changed() {
    this.generation++;
    this.persist();
    this.status('Saving progress in the background…');
    this.schedule(200);
  }
  schedule(delay) {
    clearTimeout(this.timer);
    if (!this.blocked && !this.completed && !this.ending) this.timer = setTimeout(() => this.flush(), delay);
  }
  async flush(finishing = false) {
    clearTimeout(this.timer);
    if (this.blocked || this.completed) return false;
    if (this.flight) {
      await this.flight;
      if (finishing) return this.flush(true);
      return false;
    }
    if (!finishing && !this.retry && this.generation === this.saved) return true;
    // Reconcile a lost acknowledgement before sending newer progress or completing.
    if (finishing && this.retry) {
      const ok = await this.flush();
      if (!ok || this.blocked || this.completed) return this.completed;
      return this.flush(true);
    }
    const generation = this.generation;
    const sent = this.retry || {
      finish: finishing,
      payload: JSON.parse(JSON.stringify({ revision: this.revision, state: this.state })),
    };
    const retrying = Boolean(this.retry);
    this.retry = sent;
    this.persist();
    this.status(sent.finish ? 'Ending lesson and applying schedules…' : 'Saving progress…');
    this.flight = Promise.resolve().then(async () => {
      try {
        const data = await this.request(sent.finish, sent.payload);
        this.revision = data.revision;
        this.retry = null;
        // An old request can have been retried while the user answered more questions.
        if (!retrying || LessonSync.same(this.state, sent.payload.state)) this.saved = generation;
        this.delay = 2000;
        if (data.completed_at !== null) {
          this.completed = true;
          this.state = data.state;
          this.clear();
          this.status('Lesson complete — scheduling updated.');
          this.complete(data);
        } else if (this.generation === this.saved) {
          this.clear();
          this.status('Progress saved.' + (this.localUnavailable ? ' Local browser recovery is unavailable.' : ''));
        } else this.persist();
        return true;
      } catch (error) {
        if ([400, 401, 403, 404, 409].includes(error.status)) this.fail(error.message, error.status);
        else {
          this.status('Not synced yet — retrying in the background. You can keep answering; keep this tab open if browser storage is unavailable.');
          this.delay = Math.min(this.delay * 2, 30000);
        }
        return false;
      } finally {
        this.flight = null;
        if (this.retry || this.generation !== this.saved) this.schedule(this.retry ? this.delay : 0);
      }
    });
    return this.flight;
  }
  async finish() {
    this.ending = true;
    try { return await this.flush(true); }
    finally {
      this.ending = false;
      if (this.retry || this.generation !== this.saved) this.schedule(this.delay);
    }
  }
}
