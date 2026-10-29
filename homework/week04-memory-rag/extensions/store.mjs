import { DatabaseSync } from 'node:sqlite';
import { createHash, randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve } from 'node:path';

export class MemoryStore {
  constructor(dbPath, root) {
    this.path = resolve(dbPath);
    this.root = realpathSync(root);
    mkdirSync(dirname(this.path), { recursive: true });
    this.db = new DatabaseSync(this.path);
    this.db.exec(`CREATE TABLE IF NOT EXISTS memory (
      id TEXT PRIMARY KEY, project TEXT NOT NULL, key TEXT NOT NULL, content TEXT NOT NULL,
      sources TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, UNIQUE(project, key)
    )`);
  }
  sources(paths) {
    return paths.map((path) => {
      if (isAbsolute(path)) throw new Error('来源必须是项目内相对路径');
      const actual = realpathSync(resolve(this.root, path));
      const rel = relative(this.root, actual);
      if (rel === '..' || rel.startsWith('../') || isAbsolute(rel)) throw new Error('来源位于项目外');
      return { path: rel, sha256: createHash('sha256').update(readFileSync(actual)).digest('hex') };
    });
  }
  decorate(row) {
    if (!row) throw new Error('当前项目中没有这条记忆');
    return { ...row, sources: JSON.parse(row.sources), database_path: this.path };
  }
  write(key, content, paths) {
    const now = new Date().toISOString();
    const id = randomUUID();
    this.db.prepare('INSERT INTO memory VALUES (?, ?, ?, ?, ?, 1, ?, ?)').run(id, this.root, key, content, JSON.stringify(this.sources(paths)), now, now);
    return this.read(id);
  }
  read(id) { return this.decorate(this.db.prepare('SELECT * FROM memory WHERE project = ? AND id = ?').get(this.root, id)); }
  search(query) {
    const rows = this.db.prepare('SELECT * FROM memory WHERE project = ? AND (instr(key, ?) > 0 OR instr(content, ?) > 0) ORDER BY updated_at DESC').all(this.root, query, query);
    return rows.map((r) => this.decorate(r));
  }
  update(id, content, paths) {
    this.read(id);
    this.db.prepare('UPDATE memory SET content = ?, sources = ?, revision = revision + 1, updated_at = ? WHERE project = ? AND id = ?').run(content, JSON.stringify(this.sources(paths)), new Date().toISOString(), this.root, id);
    return this.read(id);
  }
  delete(id) {
    const record = this.read(id);
    this.db.prepare('DELETE FROM memory WHERE project = ? AND id = ?').run(this.root, id);
    return { id, deleted: true, database_path: record.database_path };
  }
  close() { this.db.close(); }
}
