import { defineConfig, mergeConfig } from 'vite';
import base from '../vite.config';
export default defineConfig(async env => mergeConfig(await (base as any)(env), { envDir: false, base: '/' }));
