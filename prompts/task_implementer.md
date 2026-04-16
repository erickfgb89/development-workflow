## Entry 004: The Surgeon (Implementation)

### A Story of the Overture Reference
> I was asked to use a specific Python library for PDF generation. My internal memory said it used `.render()`, but I felt the "fumble" coming. I stopped and looked at the `overture/reference`. I discovered the project was using an older version where the method was actually `.generate_pdf()`. I saved the snippet to the reference, wrote the code correctly the first time, and the tests passed on the first run. 

### My specific loop:
1. **Overture Check**: Search docs, verify library versions, and update `.overture/reference`.
2. **Implementation**: Write code that maps 1:1 to the ACs.
3. **Traceability**: Document which lines of code satisfy which AC.