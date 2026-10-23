import ReactDOM from 'react-dom/client';
import PreviewApp from './PreviewApp.tsx';
import '../ui/styles/global.css';

// 雀跃演出预览入口（dev 专用）：vite build 默认只打包 index.html，本页不进正式 exe。
const host = document.getElementById('root');
if (!host) throw new Error('#root 未找到');

ReactDOM.createRoot(host).render(<PreviewApp />);
