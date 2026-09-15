/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        base: '#121212',
        panel: '#1E1E1E',
        edge: '#2A2A2A',
        ink: '#E5E5E5',
        muted: '#9CA3AF',
        accent: {
          start: '#FF4D4D',
          end: '#FF8A4D'
        },
        secondary: '#3B82F6'
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif']
      },
      boxShadow: {
        panel: '0 2px 12px rgba(0, 0, 0, 0.35)',
        lift: '0 14px 36px rgba(0, 0, 0, 0.55)'
      }
    }
  },
  plugins: []
}
