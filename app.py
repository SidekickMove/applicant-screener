import streamlit as st
import pandas as pd
import os
import core_logic  # This module includes process_applicants and append_first_8_columns_to_google_sheet
import tempfile
import shutil

def main():
    st.title("Applicant Screener (Multi-File Upload)")

    st.markdown("""
    **Note**: By default, Streamlit has a 200 MB upload limit for all files combined.
    If your PDFs average ~2 MB each, we recommend uploading **no more than 100** at once.
    If your files are smaller (~4 KB to 500 KB), you can handle more.
    For large sets, consider splitting them into multiple uploads.
    """)

    # 1) Basic text inputs for job title and exclude answers
    job_title = st.text_input("Job Title (for Google Sheets):", "")
    exclude_answers = st.checkbox("Exclude answers in checks?", value=False)

    # 2) CSV file upload for applicants data
    csv_file = st.file_uploader("Upload Applicants CSV File:", type=["csv"])
    if csv_file is None:
        st.info("Please upload a CSV file to begin.")
        return

    # 3) Multiple PDF/DOCX uploads
    uploaded_files = st.file_uploader(
        "Upload PDF/DOCX resumes for all applicants (multiple allowed):",
        type=["pdf", "docx"],
        accept_multiple_files=True
    )
    if not uploaded_files:
        st.info("Please upload one or more PDF or DOCX files.")
        return

    # 4) Checkboxes for symbol checks
    check_dollar = st.checkbox("Check for $ symbol", value=False)
    check_percent = st.checkbox("Check for % symbol", value=False)

    # 5) Keywords
    required_text = st.text_area("Required Keywords:", "", height=80)
    optional_text = st.text_area("Optional Keywords:", "", height=80)
    related_text = st.text_area("Related Keywords:", "", height=80)

    st.write("Click 'Start Processing' once you've provided all inputs.")

    # 6) Button to trigger processing
    if st.button("Start Processing"):
        # Validate inputs
        if csv_file is None or not uploaded_files:
            st.error("Please upload both the CSV file and at least one PDF/DOCX file.")
            return

        # If user gave a job title, incorporate it into related keywords
        if job_title.strip():
            if related_text.strip():
                related_text += f"\n{job_title.strip()}"
            else:
                related_text = job_title.strip()

        # 7) Create a temp folder to store everything
        temp_dir = tempfile.mkdtemp()

        # 7a) Save the CSV to disk
        csv_path = os.path.join(temp_dir, "candidates.csv")
        with open(csv_path, "wb") as f:
            f.write(csv_file.read())

        # 7b) Create a subfolder for PDF/DOCX files
        pdf_folder_path = os.path.join(temp_dir, "resumes")
        os.makedirs(pdf_folder_path, exist_ok=True)
        for up_file in uploaded_files:
            file_path = os.path.join(pdf_folder_path, up_file.name)
            with open(file_path, "wb") as f:
                f.write(up_file.read())

        # 8) Call process_applicants so that detailed_results.csv is created
        (filtered_df,
         pdf_exists_count,
         english_count,
         short_answers_okay_count,
         no_unallowed_count,
         keywords_count,
         final_pass_count) = core_logic.process_applicants(
                csv_path,
                pdf_folder_path,
                check_dollar,
                check_percent,
                required_text,
                optional_text,
                related_text,
                exclude_answers
         )

        # 9) Append results to Google Sheets (and core_logic will display a clickable link)
        appended_info = ""
        if job_title.strip() and not filtered_df.empty:
            num_to_append = len(filtered_df)
            core_logic.append_first_8_columns_to_google_sheet(filtered_df, job_title.strip())
            appended_info = f"Appended {num_to_append} rows to worksheet '{job_title.strip()}' in your Google Sheet!"
        else:
            appended_info = "No job title or no passing rows => skipping Google Sheets append."

        # 10) Optionally save filtered results locally for your reference
        filtered_df.to_csv("filtered_applicants.csv", index=False)

        # 11) Build and display summary message
        summary_msg = f"""
========== Check Results ==========
PDF exists:             {pdf_exists_count}
English PDF text:       {english_count}
Short answers pass:     {short_answers_okay_count}
No unallowed words:     {no_unallowed_count}
Keyword match:          {keywords_count}
Passed all checks:      {final_pass_count}
===================================
Number of rows that passed all checks: {len(filtered_df)}
{appended_info}
        """
        st.success(summary_msg)

        # 12) Clean up temporary files
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_app():
    main()


if __name__ == "__main__":
    run_app()
