/**
 * dag.js — D3 force-directed DAG renderer for the Overture dashboard.
 *
 * Exported function: renderDAG(svgEl, wus)
 *   svgEl  — the SVG DOM element to render into
 *   wus    — object mapping wu_id → WU object from state.json
 *
 * Exported function: destroyDAG(svgEl)
 *   Tears down any running simulation on the SVG.
 *
 * Node click events dispatch a CustomEvent "wu-select" on `document`
 * with detail = { wu_id }.
 */

const STATUS_COLORS = {
  pending:     { fill: '#1a1d27', stroke: '#5c6380', text: '#9aa0b8' },
  in_progress: { fill: '#0d1929', stroke: '#4f8ef7', text: '#4f8ef7' },
  completed:   { fill: '#0d1e15', stroke: '#2ead6e', text: '#2ead6e' },
  failed:      { fill: '#1e0d0d', stroke: '#e05252', text: '#e05252' },
  blocked:     { fill: '#1e1408', stroke: '#d48a1c', text: '#d48a1c' },
};

const NODE_W = 180;
const NODE_H = 56;

// Simulation handles keyed by SVG element
const _simulations = new Map();

/**
 * Render (or re-render) the DAG into `svgEl`.
 * Tears down any existing simulation first.
 */
function renderDAG(svgEl, wus) {
  destroyDAG(svgEl);

  const entries = Object.entries(wus || {});
  if (!entries.length) return;

  const svg = d3.select(svgEl);
  svg.selectAll('*').remove();

  const rect = svgEl.getBoundingClientRect();
  const W = rect.width  || 900;
  const H = rect.height || 520;

  // Defs: arrowhead
  const defs = svg.append('defs');
  defs.append('marker')
    .attr('id', 'dag-arrow')
    .attr('viewBox', '0 0 10 10')
    .attr('refX', NODE_W / 2 + 2)
    .attr('refY', 5)
    .attr('markerWidth', 8)
    .attr('markerHeight', 8)
    .attr('orient', 'auto-start-reverse')
    .append('path')
      .attr('d', 'M 0 0 L 10 5 L 0 10 z')
      .attr('fill', '#3d4260');

  // Glow filter for in_progress
  const filter = defs.append('filter').attr('id', 'dag-glow').attr('x', '-20%').attr('y', '-20%').attr('width', '140%').attr('height', '140%');
  filter.append('feGaussianBlur').attr('stdDeviation', 4).attr('result', 'blur');
  filter.append('feMerge').selectAll('feMergeNode').data(['blur', 'SourceGraphic']).enter()
    .append('feMergeNode').attr('in', d => d);

  // Build nodes + links
  const nodes = entries.map(([id, wu]) => ({
    id,
    title: wu.title || id,
    status: wu.status || 'pending',
    wu,
  }));

  const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));
  const links = [];
  entries.forEach(([id, wu]) => {
    (wu.dependencies || []).forEach(depId => {
      if (nodeById[depId]) {
        links.push({ source: depId, target: id });
      }
    });
  });

  // Layers (groups)
  const gLinks  = svg.append('g').attr('class', 'dag-links');
  const gNodes  = svg.append('g').attr('class', 'dag-nodes');

  // Force simulation
  const sim = d3.forceSimulation(nodes)
    .force('link',   d3.forceLink(links).id(d => d.id).distance(220).strength(0.6))
    .force('charge', d3.forceManyBody().strength(-600))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide(NODE_W * 0.7));

  _simulations.set(svgEl, sim);

  // Link elements
  const link = gLinks.selectAll('line')
    .data(links)
    .enter().append('line')
      .attr('stroke', '#3d4260')
      .attr('stroke-width', 1.5)
      .attr('marker-end', 'url(#dag-arrow)');

  // Node groups
  const node = gNodes.selectAll('g')
    .data(nodes)
    .enter().append('g')
      .attr('class', 'dag-node')
      .style('cursor', 'pointer')
      .call(
        d3.drag()
          .on('start', (event, d) => {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on('end', (event, d) => {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null; d.fy = null;
          })
      )
      .on('click', (event, d) => {
        event.stopPropagation();
        document.dispatchEvent(new CustomEvent('wu-select', { detail: { wu_id: d.id } }));
      });

  // Node backgrounds
  node.append('rect')
    .attr('x', -NODE_W / 2)
    .attr('y', -NODE_H / 2)
    .attr('width', NODE_W)
    .attr('height', NODE_H)
    .attr('rx', 10)
    .attr('ry', 10)
    .attr('fill',   d => (STATUS_COLORS[d.status] || STATUS_COLORS.pending).fill)
    .attr('stroke', d => (STATUS_COLORS[d.status] || STATUS_COLORS.pending).stroke)
    .attr('stroke-width', 1.8)
    .attr('filter', d => d.status === 'in_progress' ? 'url(#dag-glow)' : null);

  // WU ID label
  node.append('text')
    .attr('x', -NODE_W / 2 + 12)
    .attr('y', -NODE_H / 2 + 17)
    .attr('font-family', "'JetBrains Mono', monospace")
    .attr('font-size', 10)
    .attr('fill', d => (STATUS_COLORS[d.status] || STATUS_COLORS.pending).text)
    .text(d => d.id);

  // Title label (truncated)
  node.append('text')
    .attr('x', -NODE_W / 2 + 12)
    .attr('y', NODE_H / 2 - 12)
    .attr('font-family', "Inter, system-ui, sans-serif")
    .attr('font-size', 12.5)
    .attr('font-weight', '600')
    .attr('fill', d => (STATUS_COLORS[d.status] || STATUS_COLORS.pending).text)
    .text(d => truncate(d.title, 22));

  // Pulse animation for in_progress
  node.filter(d => d.status === 'in_progress')
    .append('circle')
      .attr('cx', NODE_W / 2 - 12)
      .attr('cy', -NODE_H / 2 + 12)
      .attr('r', 4)
      .attr('fill', '#4f8ef7')
      .call(animatePulse);

  // Tick
  sim.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // Zoom
  const zoom = d3.zoom()
    .scaleExtent([0.2, 3])
    .on('zoom', (event) => {
      gLinks.attr('transform', event.transform);
      gNodes.attr('transform', event.transform);
    });
  svg.call(zoom);

  // Dismiss popover on canvas click
  svg.on('click', () => {
    document.dispatchEvent(new CustomEvent('dag-canvas-click'));
  });

  // Store zoom for fit-to-screen
  svgEl._dagZoom = zoom;
  svgEl._dagSim  = sim;
}

function destroyDAG(svgEl) {
  const sim = _simulations.get(svgEl);
  if (sim) { sim.stop(); _simulations.delete(svgEl); }
  if (svgEl) {
    d3.select(svgEl).on('.zoom', null).selectAll('*').remove();
    svgEl._dagZoom = null;
    svgEl._dagSim  = null;
  }
}

function fitToScreen(svgEl) {
  if (!svgEl || !svgEl._dagZoom) return;
  const svg   = d3.select(svgEl);
  const rect  = svgEl.getBoundingClientRect();
  const g     = svg.select('.dag-nodes');
  const bbox  = g.node()?.getBBox?.();
  if (!bbox || !bbox.width) return;

  const scale = Math.min(0.95, Math.min(rect.width / (bbox.width + 60), rect.height / (bbox.height + 60)));
  const tx = rect.width  / 2 - scale * (bbox.x + bbox.width  / 2);
  const ty = rect.height / 2 - scale * (bbox.y + bbox.height / 2);
  svg.transition().duration(400).call(
    svgEl._dagZoom.transform,
    d3.zoomIdentity.translate(tx, ty).scale(scale)
  );
}

function animatePulse(sel) {
  function repeat() {
    sel.attr('r', 4).attr('opacity', 0.8)
      .transition().duration(700).attr('r', 7).attr('opacity', 0.2)
      .transition().duration(700).attr('r', 4).attr('opacity', 0.8)
      .on('end', repeat);
  }
  repeat();
}

function truncate(str, maxLen) {
  if (!str) return '';
  return str.length > maxLen ? str.slice(0, maxLen - 1) + '…' : str;
}

// Exports
window.OvertureDAG = { renderDAG, destroyDAG, fitToScreen };
