/** Browser entry point. Stylesheet order matters: tokens, fonts, reset, app, scenes. */

import React from 'react'
import ReactDOM from 'react-dom/client'

import './styles/tokens.css'
import './styles/fonts.css'
import './styles/base.css'
import './styles/app.css'
import './styles/scenes.css'

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
