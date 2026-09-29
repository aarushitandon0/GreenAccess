/** Browser entry point. Stylesheet order matters: tokens, fonts, reset, app, scenes, cinema, chapters, layout. */

import React from 'react'
import ReactDOM from 'react-dom/client'

import './styles/tokens.css'
import './styles/fonts.css'
import './styles/base.css'
import './styles/app.css'
import './styles/scenes.css'
import './styles/cinema.css'
import './styles/chapters.css'
import './styles/layout.css'

import { App } from './App'

const container = document.getElementById('root')
if (!container) {
  throw new Error('Root element #root not found')
}

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
