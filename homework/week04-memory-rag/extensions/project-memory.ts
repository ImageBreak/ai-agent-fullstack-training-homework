import { Type } from 'typebox';
import { resolve } from 'node:path';
import { MemoryStore } from './store.mjs';

export default function extension(pi: any) {
  const env = process.env;
  if (env.HARNESS_MODEL && env.HARNESS_BASE_URL && env.HARNESS_API_KEY) {
    pi.registerProvider('homework-deepseek', {
      name: 'Homework DeepSeek', baseUrl: env.HARNESS_BASE_URL,
      apiKey: '$HARNESS_API_KEY', api: 'openai-completions',
      models: [{ id: env.HARNESS_MODEL, name: env.HARNESS_MODEL, reasoning: true,
        input: ['text'], contextWindow: 32768, maxTokens: 4096,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        compat: { supportsStore: false, supportsDeveloperRole: false,
          maxTokensField: 'max_tokens', requiresReasoningContentOnAssistantMessages: true,
          thinkingFormat: 'deepseek' } }],
    });
  }
  const dbPath = resolve(env.HOMEWORK_MEMORY_DB || '.memory/memory.sqlite3');
  const store = new MemoryStore(dbPath, process.cwd());
  const str = () => Type.String({ minLength: 1 });
  const paths = () => Type.Array(str(), { minItems: 1 });
  const tools = [
    ['memory_write', '写入当前项目长期记忆，sources 为已读取的项目内相对文件路径。', Type.Object({key:str(),content:str(),sources:paths()}), (p:any)=>store.write(p.key,p.content,p.sources)],
    ['memory_search', '按关键词查找当前项目的长期记忆。', Type.Object({query:str()}), (p:any)=>store.search(p.query)],
    ['memory_read', '按记录 ID 读取当前项目的长期记忆。', Type.Object({id:str()}), (p:any)=>store.read(p.id)],
    ['memory_update', '更新当前项目已有记录，保留 ID 并更新来源和版本。', Type.Object({id:str(),content:str(),sources:paths()}), (p:any)=>store.update(p.id,p.content,p.sources)],
    ['memory_delete', '删除当前项目指定 ID 的长期记忆。', Type.Object({id:str()}), (p:any)=>store.delete(p.id)],
  ];
  for (const [name, description, parameters, fn] of tools as any[]) {
    pi.registerTool({ name, label: name, description, parameters,
      async execute(_id:any, params:any) {
        try { const result = fn(params); return { content:[{type:'text',text:JSON.stringify(result,null,2)}], details:result }; }
        catch (e:any) { return {content:[{type:'text',text:String(e.message || e)}],details:{error:true},isError:true}; }
      },
    });
  }
  pi.on('session_shutdown', async () => store.close());
}
