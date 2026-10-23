import ReactDOM from 'react-dom/client';
import App from './App';
import './ui/styles/global.css';

/**
 * ★ 刻意不使用 React.StrictMode
 *
 * StrictMode 会对同一组件实例执行「mount → unmount → mount」以暴露副作用问题。
 * 但本应用的运行时（PixiJS Stage / Layers / ScriptVM 的 pc 指针）是**有状态的长生命周期对象**：
 * 双挂载会销毁第一套画面（Layers.destroy），而 VM 进度却停留在半路——
 * 第二次挂载从中途继续，永远错过开头已执行过的 bg/bgm/sprite 指令（实测：BG 层为空）。
 *
 * 画布/游戏类应用不兼容 StrictMode 双挂载是业界常态；副作用自查由
 * `npm test`（引擎单测）与 `tools/shot.ts`（截图自检）承担。
 */
const host = document.getElementById('root');
if (!host) throw new Error('#root 未找到');

ReactDOM.createRoot(host).render(<App />);
