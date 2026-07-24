// Single source of truth for the view layer: Preact + htm bound to h, plus the
// hooks we use. Every component imports { html, useState, ... } from here, so
// htm is bound exactly once. Bare specifiers (preact, preact/hooks, htm) resolve
// via the import map in index.html to the vendored ESM files.
import { h, render, Fragment, createContext, Component } from 'preact';
import { useState, useEffect, useRef, useMemo, useContext, useCallback } from 'preact/hooks';
import htm from 'htm';

export const html = htm.bind(h);
export { h, render, Fragment, createContext, Component,
         useState, useEffect, useRef, useMemo, useContext, useCallback };
