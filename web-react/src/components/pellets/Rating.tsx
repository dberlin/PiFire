/** A 1-5 star rating with ONE accessible name.
 *
 * The glyphs are aria-hidden and the name lives on the wrapper, because this
 * page renders ratings in three separate cards (current load, profile list,
 * load log) and a bare run of `★` characters is exactly the kind of loose
 * text match this project has already lost time to. Locate a rating as
 * `getByLabelText("Rating: 4 of 5")`, never by its glyphs.
 */
export function Rating({ value }: { value: number }) {
  return (
    // role="img" because aria-label is prohibited on a bare span (role
    // generic): the star run is one graphic whose text alternative is the
    // label. This is the standard star-rating pattern.
    <span role="img" aria-label={`Rating: ${value} of 5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} aria-hidden="true" className={n <= value ? "pf-pellets-star" : undefined}>
          {n <= value ? "★" : "☆"}
        </span>
      ))}
    </span>
  );
}
