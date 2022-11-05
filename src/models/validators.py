import re
from typing import Optional

from config import logger


def percentage_and_pixels_validator(input_, values, field):
    logger.debug(f"Validating {field.name} -> {input_}")
    re_match = re.match(r"(0*?\.?\d*\.?\d*?)(%)?$", str(input_), re.IGNORECASE)
    if re_match:
        try:
            starts_with: Optional[re.Match] = None
            type_of = ''
            if re_match.group(1):
                starts_with = re.search(
                    r"(0*\.?)(\d*\.?\d*?)(%)?$", re_match.group(1), re.IGNORECASE
                )
                if re_match.group(2) == "%":
                    # Explicit %
                    type_of = "Percentage"
                    input_ = float(re_match.group(1)) / 100.0
                elif re_match.group(2) == "px":
                    # Explicit px
                    type_of = "Pixel"
                    input_ = int(re_match.group(1))
                elif starts_with and not starts_with.group(1):
                    # there is no '%' or 'px' at end and the string does not start with 0*. or .
                    # consider it a pixel input (basically an int)
                    type_of = "Pixel"
                    input_ = int(re_match.group(1))
                else:
                    # String starts with 0*. or . treat as a float type percentile
                    type_of = "Percentage"
                    input_ = float(re_match.group(1))
                logger.debug(
                    f"{type_of} value detected for {field.name} ({input_})"
                )
        except TypeError or ValueError as e:
            logger.warning(
                f"{field.name} value: '{input_}' could not be converted to a FLOAT! -> {e} "
            )
            input_ = 1
    else:
        logger.warning(
            f"{field.name} value: '{input_}' malformed!"
        )
        input_ = 1
    return input_