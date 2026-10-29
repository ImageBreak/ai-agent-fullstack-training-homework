import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { MemoryStore } from '../extensions/store.mjs';

test('持久化、隔离、更新与删除', () => {
  const root = mkdtempSync(join(tmpdir(),'memory-test-'));
  try {
    const a = join(root,'a'), b = join(root,'b'), db = join(root,'db.sqlite');
    mkdirSync(a); mkdirSync(b); writeFileSync(join(a,'source.txt'),'first');
    let s = new MemoryStore(db,a); const saved=s.write('session','test path',['source.txt']); s.close();
    const child = spawnSync(process.execPath,['--input-type=module','-e',`import {MemoryStore} from ${JSON.stringify(new URL('../extensions/store.mjs',import.meta.url).href)}; const s=new MemoryStore(process.argv[1],process.argv[2]); console.log(JSON.stringify(s.search('session'))); s.close();`,db,a],{encoding:'utf8'});
    assert.equal(child.status,0); assert.equal(JSON.parse(child.stdout)[0].id,saved.id);
    const other = new MemoryStore(db,b); assert.deepEqual(other.search('session'),[]); assert.throws(()=>other.read(saved.id)); other.close();
    s=new MemoryStore(db,a); writeFileSync(join(a,'source.txt'),'second');
    const updated=s.update(saved.id,'new path',['source.txt']); assert.equal(updated.id,saved.id); assert.equal(updated.revision,2); assert.notEqual(updated.sources[0].sha256,saved.sources[0].sha256);
    assert.throws(()=>s.write('escape','bad',['../db.sqlite']));
    assert.equal(s.delete(saved.id).deleted,true); assert.deepEqual(s.search('session'),[]); s.close();
  } finally { rmSync(root,{recursive:true,force:true}); }
});
