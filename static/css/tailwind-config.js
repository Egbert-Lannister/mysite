// Tailwind CSS 配置 (与 base.html 中的配置保持同步)
tailwind.config = {
    darkMode: 'class',
    theme: {
        extend: {
            fontFamily: {
                sans: ['Noto Sans SC', 'system-ui', 'sans-serif'],
                mono: ['JetBrains Mono', 'monospace'],
            },
            colors: {
                primary: {
                    50: '#faf5ff', 100: '#f3e8ff', 200: '#e9d5ff',
                    300: '#d8b4fe', 400: '#c084fc', 500: '#a855f7',
                    600: '#9333ea', 700: '#7c3aed', 800: '#6b21a8', 900: '#581c87',
                }
            }
        }
    }
}
