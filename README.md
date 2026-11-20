# CAD De-ID pipeline

Work in progress. This folder will hold the full pipeline source, lockfiles and exact run instructions. Each attempt uploads it as its `code/` folder, as the challenge requires.

Planned order of work:
1. Build one global identity map: owners, organizations, people, emails, domains, project and part names.
2. Rename paths consistently, and rewrite the file references inside other files to match.
3. Rewrite text formats: STEP headers, DXF, SVG, IGES.
4. Edit the XML inside zip-based formats: FreeCAD, 3MF, SolidWorks 2015+ document properties.
5. Rewrite PDF metadata, then visible text.
6. Process archive contents.
7. Leave the rest byte-identical apart from renaming, and document that in README.txt.
