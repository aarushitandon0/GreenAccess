/**
 * Chapter: what the page weighs.
 *
 * Single responsibility: show where a page's transfer size goes, and what the
 * detectors found that could be lighter.
 *
 * Every carbon figure here is an estimate from the Sustainable Web Design model
 * and is labelled as one. The byte counts are not estimates — they are measured
 * transfer sizes from the browser's own network events — and the two are kept
 * visibly distinct rather than blurred into one number.
 */

import { useMemo } from 'react'

import { BytesBar } from '../components/BytesBar'
import { Scene } from '../components/Scene'
import { formatBytes, formatGrams, hostOf, pluralise, truncateMiddle } from '../lib/format'
import type { CarbonResult, Detection } from '../lib/types'

/**
 * Human headings for the detectors of MASTERSPEC §7.3.
 *
 * A detector fires once per element, so a page with ten oversized images
 * produces ten findings that differ only in a selector. Grouping them under one
 * heading is the difference between a readable chapter and forty near-identical
 * cards; the individual findings are still there, one level down.
 */
const DETECTOR_LABELS: Record<string, string> = {
  oversized_image: 'Images larger than they are displayed',
  legacy_format: 'Images in a heavier format than they need',
  no_dimensions: 'Images with no width and height',
  eager_below_fold: 'Below-the-fold images that load eagerly',
  text_in_image_suspected: 'Text that appears to be baked into an image',
  autoplay_media: 'Media that plays on its own',
  third_party_scripts: 'Scripts from other domains',
  font_bloat: 'Font files',
  uncompressed_text: 'Text served without compression',
  no_reduced_motion: 'Animation with no reduced-motion handling',
}

/** Title case for an unrecognised detector, rather than showing its raw name. */
function detectorLabel(detector: string): string {
  const known = DETECTOR_LABELS[detector]
  if (known) {
    return known
  }
  const words = detector.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

const IMAGE_ISSUE_LABELS: Record<string, string> = {
  oversized: 'Oversized',
  legacy_format: 'Legacy format',
  no_dimensions: 'No dimensions',
  eager_below_fold: 'Loads eagerly below the fold',
  text_in_image_suspected: 'Text in image (suspected)',
}

export interface CarbonProps {
  carbon: CarbonResult
}

export function Carbon({ carbon }: CarbonProps): JSX.Element {
  // The heaviest images first: that is the order anyone would fix them in.
  const images = [...carbon.images].sort((a, b) => b.bytes - a.bytes).slice(0, 10)
  const savings = carbon.detections.reduce(
    (total, detection) => total + detection.estimated_saving_bytes,
    0,
  )

  // One card per detector, heaviest saving first, with its findings inside.
  const groups = useMemo(() => {
    const byDetector = new Map<string, Detection[]>()
    for (const detection of carbon.detections) {
      byDetector.set(detection.detector, [
        ...(byDetector.get(detection.detector) ?? []),
        detection,
      ])
    }
    return [...byDetector.entries()]
      .map(([detector, items]) => ({
        detector,
        items,
        saving: items.reduce((total, item) => total + item.estimated_saving_bytes, 0),
      }))
      .sort((a, b) => b.saving - a.saving || b.items.length - a.items.length)
  }, [carbon.detections])

  return (
    <Scene
      id="carbon"
      title="What the page weighs"
      lede={`${formatBytes(carbon.total_bytes)} over ${pluralise(carbon.request_count, 'request')}, which the Sustainable Web Design model puts at ${formatGrams(carbon.grams_per_view)} of CO₂ per view.`}
      className="scene--data"
    >
      <BytesBar
        byType={carbon.by_type}
        total={carbon.total_bytes}
        caption="Measured transfer size by resource type. These are bytes on the wire, not estimates."
      />

      {carbon.detections.length > 0 ? (
        <section className="detections" aria-labelledby="detections-title">
          <h3 id="detections-title">What could be lighter</h3>
          <p className="scene__sub">
            {pluralise(carbon.detections.length, 'finding')}, worth roughly{' '}
            {formatBytes(savings)} if every one were addressed.{' '}
            <span className="chip chip--estimate">Estimate</span>
          </p>
          {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
          <ul className="detection-list" role="list">
            {groups.map((group) => (
              <li className="detection" key={group.detector}>
                <h4 className="detection__title">
                  {detectorLabel(group.detector)}
                  <span className="detection__count">
                    {pluralise(group.items.length, 'finding')}
                  </span>
                </h4>
                <p className="detection__saving">
                  {group.saving > 0
                    ? `About ${formatBytes(group.saving)} lighter in total`
                    : 'No byte saving; affects how the page behaves'}
                </p>

                <details className="detection__items">
                  <summary>
                    {group.items.length === 1 ? 'Show the finding' : 'Show each finding'}
                  </summary>
                  {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
                  <ul className="detection__sublist" role="list">
                    {group.items.map((detection, index) => (
                      <li key={index}>
                        <p className="detection__summary">{detection.summary}</p>
                        {detection.evidence.length > 0 ? (
                          <p className="detection__evidence">
                            {detection.evidence.slice(0, 2).map((item, evidenceIndex) => (
                              <code key={evidenceIndex}>{truncateMiddle(item, 46)}</code>
                            ))}
                            {detection.evidence.length > 2 ? (
                              <span className="detection__more">
                                and {detection.evidence.length - 2} more
                              </span>
                            ) : null}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </details>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {carbon.third_party.requests > 0 ? (
        <section className="third-party" aria-labelledby="third-party-title">
          <h3 id="third-party-title">Third parties</h3>
          <p className="scene__sub">
            {pluralise(carbon.third_party.requests, 'request')} to{' '}
            {pluralise(carbon.third_party.hosts.length, 'other domain')}, carrying{' '}
            {formatBytes(carbon.third_party.bytes)} — of which{' '}
            {formatBytes(carbon.third_party.script_bytes)} is script.
          </p>
          {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
          <ul className="host-list" role="list">
            {carbon.third_party.hosts.map((host) => (
              <li className="host" key={host}>
                <code>{host}</code>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {images.length > 0 ? (
        <section className="images" aria-labelledby="images-title">
          <h3 id="images-title">The heaviest images</h3>
          <table className="data-table">
            <caption className="visually-hidden">
              The ten heaviest images on the page, with the issues detected for each
            </caption>
            <thead>
              <tr>
                <th scope="col">Image</th>
                <th scope="col">Size</th>
                <th scope="col">Natural</th>
                <th scope="col">Rendered</th>
                <th scope="col">Issues</th>
              </tr>
            </thead>
            <tbody>
              {images.map((image) => (
                <tr key={image.url}>
                  <th scope="row">
                    {/* The filename is what identifies an asset in a list. */}
                    <code>{truncateMiddle(image.url.split('/').pop() || hostOf(image.url), 34)}</code>
                  </th>
                  <td className="bytes__value">{formatBytes(image.bytes)}</td>
                  <td className="bytes__value">
                    {image.natural_w}&times;{image.natural_h}
                  </td>
                  <td className="bytes__value">
                    {image.rendered_w}&times;{image.rendered_h}
                  </td>
                  <td>
                    {image.issues.length === 0 ? (
                      <span className="muted">None</span>
                    ) : (
                      <ul className="chip-list">
                        {image.issues.map((issue) => (
                          <li key={issue}>
                            <span className="chip chip--leaf">
                              {IMAGE_ISSUE_LABELS[issue] ?? issue}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {carbon.autoplay_media.length > 0 ? (
        <section className="autoplay" aria-labelledby="autoplay-title">
          <h3 id="autoplay-title">Media that plays on its own</h3>
          {/* eslint-disable-next-line jsx-a11y/no-redundant-roles */}
          <ul className="detection-list" role="list">
            {carbon.autoplay_media.map((item) => (
              <li className="detection" key={item.url}>
                <h4 className="detection__title">
                  {item.kind === 'video' ? 'Video' : 'Animated GIF'} — {formatBytes(item.bytes)}
                </h4>
                <p className="detection__evidence">
                  <code>{truncateMiddle(item.selector, 46)}</code>
                </p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <details className="explainer">
        <summary className="explainer__summary">
          How is the carbon figure estimated?
        </summary>
        <div className="explainer__body">
          <p>
            {carbon.assumptions.model} version {carbon.assumptions.model_version}, at{' '}
            {carbon.assumptions.kwh_per_gb} kWh per GB and{' '}
            {carbon.assumptions.grid_intensity_g_per_kwh} g CO&#8322; per kWh.{' '}
            {carbon.assumptions.green_hosted
              ? 'The data-centre share uses the renewable intensity, because the host is listed as green.'
              : 'The host is not listed as green, so the global average intensity is used throughout.'}
          </p>
          <p>
            A first visit is counted at {carbon.assumptions.first_visit_percentage * 100}% of
            readers and a return visit at {carbon.assumptions.return_visit_percentage * 100}%,
            with returning readers re-fetching {carbon.assumptions.data_reload_ratio * 100}% of
            the page. That gives {formatGrams(carbon.grams_first_visit)} on a first visit and{' '}
            {formatGrams(carbon.grams_return_visit)} on a return.
          </p>
          <p className="explainer__ref">
            <a href={carbon.assumptions.source_url} target="_blank" rel="noreferrer noopener">
              The model
              <span className="visually-hidden"> (opens in a new tab)</span>
            </a>
          </p>
        </div>
      </details>
    </Scene>
  )
}
