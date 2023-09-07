/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.{html,js}", "./static/**/*.{html,js}", "./static/preline/preline.js"],
  theme: {
    extend: {},
  },
  plugins: [
    require('./static/preline/plugin'),
  ],
}